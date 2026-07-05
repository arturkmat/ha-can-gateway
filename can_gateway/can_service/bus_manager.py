from __future__ import annotations

import glob
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from can_secure_transport import SecureCanTransport
from protocol_constants import (
    COMMAND_GET_BUILD_INFO,
    COMMAND_GET_BUTTON_TIMING,
    COMMAND_GET_MCP23017_ROLE_DUMP,
    COMMAND_GET_MODULE_NAME,
    COMMAND_GET_RELAY_PULSE,
    COMMAND_GET_SHUTTER_RELAYS,
    COMMAND_GET_SUMMARY,
    COMMAND_REBOOT_MODULE,
    COMMAND_SCAN_MCP23017,
    COMMAND_SET_RELAY_STATE,
    HW_TYPE_NAME_MAP,
    MCP23017_RELAY_CAN_BASE,
    SHIFT595_RELAY_BASE_INDEX,
    SHIFT595_RELAY_COUNT_PER_REGISTER,
    UNKNOWN_MODULE_IDS,
    can_v2_config_request_id,
    can_v2_ota_data_id,
)

from .can_send import prepare_outgoing_frames
from .configurator_bridge import create_engine
from .deep_config import refresh_module_deep as _refresh_module_deep_impl
from .module_store import discovery_snapshot, load_modules, save_modules
from .module_state import (
    ModuleRuntimeState,
    RelayState,
    ShutterState,
    decode_mcp_relays_0x602,
    decode_relays_0x600,
    parse_mcp_role_dump,
)
from .options import AddonOptions, CAN_INTERFACE_GS_USB, CAN_INTERFACE_SLCAN

_LOGGER = logging.getLogger(__name__)

SERIAL_BAUD_CANDIDATES = (115200, 460800)
_gs_usb_out_ep_patch_applied = False


def _apply_cannectivity_gs_usb_out_ep_patch() -> None:
    global _gs_usb_out_ep_patch_applied
    if _gs_usb_out_ep_patch_applied:
        return
    try:
        from gs_usb.constants import GS_CAN_MODE_HW_TIMESTAMP
        from gs_usb.gs_usb import (
            GS_USB_CANNECTIVITY_PRODUCT_ID,
            GS_USB_CANNECTIVITY_VENDOR_ID,
            GsUsb,
        )
    except ImportError:
        return

    def _send(self, frame):
        hw_timestamps = (
            (self.device_flags & GS_CAN_MODE_HW_TIMESTAMP) == GS_CAN_MODE_HW_TIMESTAMP
        )
        packed = frame.pack(hw_timestamps)
        dev = self.gs_usb
        if (
            dev.idVendor == GS_USB_CANNECTIVITY_PRODUCT_ID
            and dev.idProduct == GS_USB_CANNECTIVITY_PRODUCT_ID
        ):
            dev.write(0x01, packed)
        else:
            dev.write(0x02, packed)
        return True

    GsUsb.send = _send
    _gs_usb_out_ep_patch_applied = True
_BROADCAST_ATTEMPTS = 3
_BROADCAST_GAP_S = 0.08
_PASSIVE_LISTEN_S = 4.5
_NAME_READ_GAP_S = 0.25
MAX_LOCAL_RELAYS = 16
RELAY_GPIO_ROLE = 2  # PIN_ROLE_MAP["Relay"]


@dataclass
class ModuleRecord:
    module_id: int
    hw_type: int = 255
    hw_name: str = "unknown"
    mac: str | None = None
    name: str | None = None
    firmware_build: str | None = None
    summary_details: str = ""
    button_count: int | None = None
    relay_count: int | None = None
    shutter_count: int | None = None
    last_seen_s: float = 0.0
    runtime: ModuleRuntimeState = field(default_factory=ModuleRuntimeState)

    def to_dict(self, *, include_runtime: bool = False) -> dict[str, Any]:
        out = {
            "module_id": self.module_id,
            "hw_type": self.hw_type,
            "hw_name": self.hw_name,
            "mac": self.mac,
            "name": self.name,
            "firmware_build": self.firmware_build,
            "summary_details": self.summary_details,
            "button_count": self.button_count,
            "relay_count": self.relay_count,
            "shutter_count": self.shutter_count,
            "last_seen_s": self.last_seen_s,
        }
        if include_runtime:
            out["runtime"] = self.runtime.to_dict()
        return out


class BusManager:
    """Właściciel magistrali CAN — SLCAN + gs_usb."""

    def __init__(self, options: AddonOptions) -> None:
        self._options = options
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._bus = None
        self._transport: SecureCanTransport | None = None
        self._modules: dict[int, ModuleRecord] = {}
        self._persisted_modules: dict[int, dict[str, Any]] = {}
        self._load_persisted_modules()
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None
        self._bus_error: str | None = None
        self._last_scan_status: str = "never"
        self._last_scan_at: float | None = None
        self._frame_listeners: list[Callable[[], None]] = []
        self._rx_enabled = threading.Event()
        self._rx_enabled.set()
        self._scan_lock = threading.Lock()
        self._reconnect_thread: threading.Thread | None = None
        self._active_port: str | None = None
        self._pulse_timers: dict[tuple[int, int], threading.Timer] = {}
        self._engine = None
        self._transport_macs_valid = False

    def _invalidate_transport_macs(self) -> None:
        self._transport_macs_valid = False

    def _register_mac_int(self, module_id: int, module_mac: int) -> None:
        if self._transport is None:
            return
        mac_b = bytes(
            [
                (int(module_mac) >> 40) & 0xFF,
                (int(module_mac) >> 32) & 0xFF,
                (int(module_mac) >> 24) & 0xFF,
                (int(module_mac) >> 16) & 0xFF,
                (int(module_mac) >> 8) & 0xFF,
                int(module_mac) & 0xFF,
            ]
        )
        self._transport.register_mac(int(module_id), mac_b)

    def _sync_transport_macs_from_engine(self) -> None:
        if self._transport is None:
            return
        engine = self._engine
        if engine is None:
            return
        for item in engine.discovered_modules:
            mid = item.get("module_id")
            mac = item.get("module_mac")
            if mid is None or mac is None or int(mid) in UNKNOWN_MODULE_IDS:
                continue
            self._register_mac_int(int(mid), int(mac))
        self._transport_macs_valid = True

    def _ensure_transport_macs(self) -> None:
        if self._transport_macs_valid:
            return
        self._sync_transport_macs_from_engine()

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine(self)
        return self._engine

    def start(self) -> None:
        if self._options.secure_can:
            master = self._options.master_key_bytes
            if master is not None:
                self._transport = SecureCanTransport(master_key=master)
                _LOGGER.info("Secure CAN enabled (MASTER_KEY %d bytes)", len(master))
            elif self._options.master_key_hex.strip():
                _LOGGER.error("Niepoprawny MASTER_KEY — wymagane 64 znaki hex przy secure_can=true")
        else:
            if self._options.master_key_hex.strip():
                _LOGGER.info("MASTER_KEY w opcjach ignorowany — secure_can=false (domyślny tryb V3)")
            _LOGGER.info("Plain CAN mode (V3) — brak szyfrowania CONFIG")
        self._open_bus()
        self._rx_thread = threading.Thread(target=self._rx_loop, name="can-rx", daemon=True)
        self._rx_thread.start()
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, name="can-reconnect", daemon=True)
        self._reconnect_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._rx_enabled.set()
        if self._reconnect_thread is not None:
            self._reconnect_thread.join(timeout=2.0)
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=2.0)
        self._close_bus()

    def on_modules_changed(self, listener: Callable[[], None]) -> None:
        self._frame_listeners.append(listener)

    def _notify(self) -> None:
        for fn in self._frame_listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("listener failed", exc_info=True)

    @property
    def bus_ok(self) -> bool:
        return self._bus is not None and self._bus_error is None

    def status(self) -> dict[str, Any]:
        with self._lock:
            master_ok = self._options.master_key_bytes is not None
            master_hex = self._options.master_key_hex.strip()
            master_invalid = bool(master_hex) and not master_ok
            return {
                "bus_ok": self.bus_ok,
                "bus_error": self._bus_error,
                "can_interface": self._options.can_interface,
                "can_port": self._active_port or self._options.can_port,
                "configured_port": self._options.can_port,
                "gsusb_channel": self._options.gsusb_channel,
                "can_bitrate": self._options.can_bitrate,
                "secure_enabled": self._transport is not None,
                "master_key_configured": master_ok,
                "master_key_invalid": master_invalid,
                "master_key_required_hint": (
                    None
                    if master_ok
                    else "Ustaw master_key_hex w konfiguracji dodatku (64 znaki hex) dla modułów Secure CAN"
                ),
                "module_count": len(self._modules),
                "last_scan_status": self._last_scan_status,
                "last_scan_at": self._last_scan_at,
                "version": "0.6.1",
                "mqtt_enabled": self._options.mqtt_enabled,
            }

    def full_state(self) -> dict[str, Any]:
        if self.bus_ok:
            self._ensure_transport_macs()
            self.collect_relay_state_frames(0.15)
        engine = self._get_engine()
        modules = [engine.export_module_dict(rec["module_id"]) for rec in engine.list_modules()]
        if not modules:
            modules = self.list_modules(include_runtime=True)
        if not modules:
            with self._lock:
                for rec in sorted(self._modules.values(), key=lambda r: r.module_id):
                    modules.append(rec.to_dict(include_runtime=True))
        from entity_export import build_entities_snapshot

        entities = build_entities_snapshot(modules)
        return {"status": self.status(), "modules": modules, "entities": entities}

    def _load_persisted_modules(self) -> None:
        for mod in load_modules():
            mid = mod.get("module_id")
            if isinstance(mid, int) and 1 <= mid <= 254:
                self._persisted_modules[int(mid)] = mod
        if self._persisted_modules:
            _LOGGER.info(
                "Loaded %d persisted module(s) from disk",
                len(self._persisted_modules),
            )

    def persist_discovery_state(self, *, last_scan_at: float | None = None) -> None:
        modules: list[dict[str, Any]] = []
        engine = self._get_engine()
        for rec in engine.list_modules():
            mid = int(rec["module_id"])
            detail = self.module_detail(mid)
            if isinstance(detail, dict):
                modules.append(detail)
        if not modules:
            with self._lock:
                for rec in sorted(self._modules.values(), key=lambda r: r.module_id):
                    modules.append(rec.to_dict(include_runtime=True))
        if not modules and self._persisted_modules:
            modules = list(self._persisted_modules.values())
        if not modules:
            return
        save_modules(modules, last_scan_at=last_scan_at)
        with self._lock:
            for mod in modules:
                mid = mod.get("module_id")
                if isinstance(mid, int):
                    self._persisted_modules[int(mid)] = mod
        _LOGGER.info("Persisted %d module snapshot(s) to /data", len(modules))

    def discovery_payload(self) -> dict[str, Any]:
        modules = self.list_modules(include_runtime=True)
        store = discovery_snapshot(scan_status=self._last_scan_status)
        if modules:
            store["modules"] = modules
            store["module_count"] = len(modules)
        return store

    def list_modules(self, *, include_runtime: bool = False) -> list[dict[str, Any]]:
        rows = self._get_engine().list_modules()
        if rows:
            if include_runtime:
                return [self.module_detail(int(r["module_id"])) or r for r in rows]
            return rows
        with self._lock:
            if self._persisted_modules:
                if include_runtime:
                    return list(self._persisted_modules.values())
                out = []
                for mod in self._persisted_modules.values():
                    row = dict(mod)
                    row.pop("runtime", None)
                    row.pop("control_relays", None)
                    out.append(row)
                return sorted(out, key=lambda r: r.get("module_id", 0))
            return [rec.to_dict() for rec in sorted(self._modules.values(), key=lambda r: r.module_id)]

    def module_detail(self, module_id: int) -> dict[str, Any] | None:
        mid = int(module_id)
        engine = self._get_engine()
        for rec in engine.discovered_modules:
            if rec.get("module_id") == mid:
                return engine.export_module_dict(mid)
        if mid in engine._contexts:  # noqa: SLF001
            return engine.export_module_dict(mid)
        with self._lock:
            rec = self._modules.get(mid)
            if rec is None:
                persisted = self._persisted_modules.get(mid)
                if persisted is not None:
                    return dict(persisted)
                return None
            out = rec.to_dict(include_runtime=True)
            out["control_relays"] = self._relay_controls_from_record(rec)
            return out

    def shutter_reserved_relays(self, module_id: int) -> set[int]:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return set()
            reserved: set[int] = set()
            for ro, rc in rec.runtime.shutter_map.values():
                if ro > 0:
                    reserved.add(int(ro))
                if rc > 0:
                    reserved.add(int(rc))
            return reserved

    def collect_relay_state_frames(self, timeout_s: float = 1.0) -> None:
        """Odbierz broadcast 0x600/0x602 — jak konfigurator _collect_relay_state_frames."""
        self._ensure_transport_macs()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.pump_rx(min(0.05, max(0.0, deadline - time.time())))

    def refresh_relay_telemetry(self, timeout_s: float = 0.6) -> None:
        """Passive refresh stanów przekaźników (0x600/0x601/0x602)."""
        if not self.bus_ok:
            return
        self._ensure_transport_macs()
        self.collect_relay_state_frames(timeout_s)

    def _relay_controls_from_record(self, rec: ModuleRecord) -> list[dict[str, Any]]:
        reserved: set[int] = set()
        for ro, rc in rec.runtime.shutter_map.values():
            if ro > 0:
                reserved.add(int(ro))
            if rc > 0:
                reserved.add(int(rc))
        nums: set[int] = set()
        for _gpio, role_info in rec.runtime.gpio_roles.items():
            if int(role_info.get("role", 0)) == RELAY_GPIO_ROLE:
                idx = int(role_info.get("index", 0))
                if idx > 0:
                    nums.add(idx)
        for rn, gpio in rec.runtime.relay_gpio_map.items():
            if 1 <= rn <= MAX_LOCAL_RELAYS and gpio != 255:
                nums.add(int(rn))
        regs = (rec.runtime.hw_flags >> 4) & 0x07
        if regs > 0:
            nums.update(
                range(
                    SHIFT595_RELAY_BASE_INDEX,
                    SHIFT595_RELAY_BASE_INDEX + regs * SHIFT595_RELAY_COUNT_PER_REGISTER,
                )
            )
        for chip, pins in rec.runtime.mcp_relay_pins.items():
            for lp in pins:
                nums.add(MCP23017_RELAY_CAN_BASE + int(chip) * 16 + int(lp))
        nums.update(rec.runtime.relays.keys())
        nums.difference_update(reserved)
        if not nums:
            nums = set(range(1, MAX_LOCAL_RELAYS + 1))
        state_by_no = {int(st.relay_no): st for st in rec.runtime.relays.values()}
        out: list[dict[str, Any]] = []
        for rn in sorted(nums):
            st = state_by_no.get(rn)
            pulse = rec.runtime.relay_pulse_ms.get(rn, 0)
            if st is not None and st.pulse_ms:
                pulse = st.pulse_ms
            out.append(
                {
                    "relay_no": rn,
                    "on": bool(st.on) if st is not None else False,
                    "pulse_ms": int(pulse),
                    "source": st.source if st is not None else "local",
                    "shutter_reserved": rn in reserved,
                }
            )
        return out

    def relay_pulse_ms_for(self, module_id: int, relay_no: int) -> int:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return 0
            rn = int(relay_no)
            st = rec.runtime.relays.get(rn)
            if st is not None and st.pulse_ms > 0:
                return int(st.pulse_ms)
            return int(rec.runtime.relay_pulse_ms.get(rn, 0))

    def _schedule_pulse_resync(self, module_id: int, relay_no: int, pulse_ms: int) -> None:
        key = (int(module_id), int(relay_no))
        old = self._pulse_timers.pop(key, None)
        if old is not None:
            old.cancel()

        def _resync() -> None:
            self._pulse_timers.pop(key, None)
            if not self._begin_command_io(blocking=True, timeout=2.0):
                return
            try:
                self.collect_relay_state_frames(0.45)
                self.store_relay_state(module_id, relay_no, False)
            finally:
                self._end_command_io()

        delay_s = max(0.05, (int(pulse_ms) + 80) / 1000.0)
        timer = threading.Timer(delay_s, _resync)
        timer.daemon = True
        self._pulse_timers[key] = timer
        timer.start()

    def collect_module_telemetry(self, module_id: int, timeout_s: float = 1.0) -> None:
        """Nasłuch 0x600/0x601/0x602/0x660 po GET_SUMMARY — jak konfigurator po skanie."""
        del module_id
        self.collect_relay_state_frames(timeout_s)

    def sync_relay_gpio_map_from_roles(self, module_id: int) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            for role_info in rec.runtime.gpio_roles.values():
                if int(role_info.get("role", 0)) != RELAY_GPIO_ROLE:
                    continue
                idx = int(role_info.get("index", 0))
                gpio = int(role_info.get("gpio", 0))
                if idx > 0 and gpio > 0:
                    rec.runtime.relay_gpio_map[idx] = gpio
            self._notify()

    def ensure_relay_metadata(self, module_id: int) -> None:
        """Skan MCP23017 + role dump gdy moduł ma expander (hw_flags bit3)."""
        mid = int(module_id)
        with self._lock:
            rec = self._modules.get(mid)
            hw_flags = int(rec.runtime.hw_flags) if rec is not None else 0
            need_mcp = rec is None or not rec.runtime.mcp_relay_pins
        if not ((hw_flags & 0x08) or need_mcp):
            return
        self.send_config_and_wait(mid, COMMAND_SCAN_MCP23017, timeout=1.5)
        time.sleep(0.1)
        for chip in range(8):
            self.send_config_and_wait(mid, COMMAND_GET_MCP23017_ROLE_DUMP, [chip], timeout=0.25)
            time.sleep(0.055)

    def clear_shutter_config(self, module_id: int) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.shutter_map.clear()
            rec.runtime.shutters.clear()
            self._notify()

    def load_control_tab_outputs(self, module_id: int) -> None:
        self._get_engine().load_control_tab_outputs(int(module_id))

    def relay_numbers_for_module(self, module_id: int) -> set[int]:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return set()
            nums: set[int] = set()
            for _gpio, role_info in rec.runtime.gpio_roles.items():
                if int(role_info.get("role", 0)) == RELAY_GPIO_ROLE:
                    idx = int(role_info.get("index", 0))
                    if idx > 0:
                        nums.add(idx)
            for rn, gpio in rec.runtime.relay_gpio_map.items():
                if 1 <= rn <= MAX_LOCAL_RELAYS and gpio != 255:
                    nums.add(int(rn))
            regs = (rec.runtime.hw_flags >> 4) & 0x07
            if regs > 0:
                nums.update(
                    range(
                        SHIFT595_RELAY_BASE_INDEX,
                        SHIFT595_RELAY_BASE_INDEX + regs * SHIFT595_RELAY_COUNT_PER_REGISTER,
                    )
                )
            for chip, pins in rec.runtime.mcp_relay_pins.items():
                for lp in pins:
                    nums.add(MCP23017_RELAY_CAN_BASE + int(chip) * 16 + int(lp))
            nums.update(rec.runtime.relays.keys())
            reserved: set[int] = set()
            for ro, rc in rec.runtime.shutter_map.values():
                if ro > 0:
                    reserved.add(int(ro))
                if rc > 0:
                    reserved.add(int(rc))
            nums.difference_update(reserved)
            return nums or set(range(1, 17))

    def _slcan_port_candidates(self) -> list[str]:
        ports: list[str] = []
        configured = str(self._options.can_port or "").strip()
        if configured:
            ports.append(configured)
        for pattern in ("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*"):
            try:
                for path in sorted(glob.glob(pattern)):
                    if path not in ports:
                        ports.append(path)
            except OSError:
                continue
        return ports

    def ensure_bus(self) -> bool:
        if self._bus is not None and self._bus_error is None:
            return True
        return self._try_reopen()

    def _try_reopen(self) -> bool:
        if self._stop.is_set():
            return False
        self._close_bus()
        self._open_bus()
        if self._bus is not None:
            with self._lock:
                self._bus_error = None
            _LOGGER.info("CAN bus reconnected (%s)", self._active_port or self._options.can_port)
            return True
        return False

    def _reconnect_loop(self) -> None:
        while not self._stop.wait(5.0):
            if self._bus is None:
                self._try_reopen()

    def _begin_exclusive_io(self) -> bool:
        if not self._scan_lock.acquire(blocking=False):
            return False
        self._rx_enabled.clear()
        return True

    def _end_exclusive_io(self) -> None:
        self._rx_enabled.set()
        self._scan_lock.release()

    def _begin_command_io(self, *, blocking: bool = False, timeout: float = 3.0) -> bool:
        """Blokada magistrali na krótkie polecenia (bez wyłączania wątku RX)."""
        if blocking:
            return self._scan_lock.acquire(blocking=True, timeout=timeout)
        return self._scan_lock.acquire(blocking=False)

    def _end_command_io(self) -> None:
        self._scan_lock.release()

    def _relay_cache_row(self, module_id: int, relay_no: int) -> dict[str, Any] | None:
        detail = self.module_detail(int(module_id))
        relays = (detail or {}).get("runtime", {}).get("relays") or []
        for row in relays:
            if int(row.get("relay_no", -1)) == int(relay_no):
                return row
        return None

    def _open_bus(self) -> None:
        import can

        iface = self._options.can_interface
        bitrate = self._options.can_bitrate
        if iface == CAN_INTERFACE_GS_USB:
            try:
                _apply_cannectivity_gs_usb_out_ep_patch()
                self._bus = can.Bus(
                    interface="gs_usb",
                    channel=int(self._options.gsusb_channel),
                    bitrate=bitrate,
                )
                self._bus_error = None
                _LOGGER.info("gs_usb channel %d (CAN %d)", self._options.gsusb_channel, bitrate)
                return
            except Exception as err:  # noqa: BLE001
                self._bus_error = f"Cannot open gs_usb channel {self._options.gsusb_channel}: {err}"
                _LOGGER.error(self._bus_error)
                return

        if iface != CAN_INTERFACE_SLCAN:
            self._bus_error = f"Nieobsługiwany can_interface: {iface}"
            return

        last_err: Exception | None = None
        baud_candidates: list[int] = []
        for baud in (self._options.tty_baudrate, *SERIAL_BAUD_CANDIDATES):
            if baud not in baud_candidates:
                baud_candidates.append(baud)
        for port in self._slcan_port_candidates():
            if not port:
                continue
            try:
                import os

                if not os.path.exists(port):
                    continue
            except OSError:
                continue
            for serial_baud in baud_candidates:
                try:
                    self._bus = can.Bus(
                        interface="slcan",
                        channel=port,
                        bitrate=bitrate,
                        ttyBaudrate=serial_baud,
                    )
                    self._active_port = port
                    self._bus_error = None
                    _LOGGER.info("SLCAN open %s @ %d (CAN %d)", port, serial_baud, bitrate)
                    return
                except Exception as err:  # noqa: BLE001
                    last_err = err
        configured = self._options.can_port
        self._active_port = None
        self._bus_error = f"Cannot open SLCAN (configured {configured}): {last_err}"
        _LOGGER.error(self._bus_error)

    def _close_bus(self) -> None:
        bus = None
        try:
            with self._io_lock:
                bus = self._bus
                self._bus = None
        except Exception:  # noqa: BLE001
            _LOGGER.debug("bus close error", exc_info=True)
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("bus shutdown error", exc_info=True)

    def _on_io_error(self, err: Exception) -> None:
        _LOGGER.warning("CAN I/O error — closing port, reconnect pending: %s", err)
        with self._lock:
            self._bus_error = str(err)
        self._close_bus()
        self._active_port = None

    def _recv(self, timeout: float):
        if self._bus is None:
            return None
        try:
            with self._io_lock:
                if self._bus is None:
                    return None
                return self._bus.recv(timeout=timeout)
        except Exception as err:  # noqa: BLE001
            self._on_io_error(err)
            return None

    def _send_message(self, message) -> bool:
        if self._bus is None:
            return False
        try:
            with self._io_lock:
                if self._bus is None:
                    return False
                self._bus.send(message)
            return True
        except Exception as err:  # noqa: BLE001
            self._on_io_error(err)
            return False

    def _normalize_message(self, message):
        if message is None:
            return None
        if self._transport is None:
            return message
        peer = list(message.data)[0] if message.data else 0
        unwrapped = self._transport.unwrap_incoming(
            message.arbitration_id,
            bytes(message.data),
            default_peer=peer,
        )
        if unwrapped is None:
            return None
        can_id, ext, data = unwrapped
        import can

        return can.Message(arbitration_id=can_id, is_extended_id=ext, data=list(data))

    def _register_mac(self, module_id: int, mac_text: str) -> None:
        if self._transport is None or not mac_text:
            return
        parts = mac_text.replace("-", ":").split(":")
        if len(parts) != 6:
            return
        try:
            mac_b = bytes(int(p, 16) for p in parts)
        except ValueError:
            return
        self._transport.register_mac(module_id, mac_b)

    def _get_module(self, module_id: int) -> ModuleRecord | None:
        if module_id in UNKNOWN_MODULE_IDS:
            return None
        rec = self._modules.get(module_id)
        if rec is None:
            rec = ModuleRecord(module_id=module_id)
            self._modules[module_id] = rec
        rec.last_seen_s = time.time()
        return rec

    def _handle_message(self, message) -> None:
        if not self._rx_enabled.is_set():
            return
        message = self._normalize_message(message)
        if message is None:
            return
        self._get_engine().handle_can_message(message, already_normalized=True)

    def _handle_config_response_legacy(self, rec: ModuleRecord, data: list[int]) -> None:
        cmd = int(data[1])
        status = int(data[2])
        if status != 0:
            return
        if cmd == COMMAND_GET_SUMMARY:
            rec.summary_details = self._build_summary_details(data)
            if len(data) >= 8:
                rec.button_count = int(data[3])
                rec.relay_count = int(data[4])
                rec.shutter_count = int(data[6])
                rec.runtime.hw_flags = int(data[7])
            elif len(data) > 7:
                rec.runtime.hw_flags = int(data[7])
        elif cmd == COMMAND_GET_MODULE_NAME:
            rec.name = self._ascii_from_bytes(data[3:]) or None
        elif cmd == COMMAND_GET_BUILD_INFO and len(data) >= 8:
            rec.firmware_build = (
                f"{2000 + data[3]:04d}.{data[4]:02d}.{data[5]:02d} "
                f"{data[6]:02d}:{data[7]:02d}"
            )
        elif cmd == COMMAND_GET_SHUTTER_RELAYS and len(data) >= 6:
            sid = int(data[3])
            ro, rc = int(data[4]), int(data[5])
            if ro > 0 or rc > 0:
                rec.runtime.shutter_map[sid] = (ro, rc)
                sh = rec.runtime.shutters.setdefault(sid, ShutterState(shutter_no=sid))
                sh.relay_open, sh.relay_close = ro, rc
            else:
                rec.runtime.shutter_map.pop(sid, None)
                rec.runtime.shutters.pop(sid, None)
        elif cmd == COMMAND_GET_MCP23017_ROLE_DUMP and len(data) >= 7:
            chip = int(data[3])
            rec.runtime.mcp_relay_pins[chip] = parse_mcp_role_dump(data[4:8])
        elif cmd == COMMAND_GET_RELAY_PULSE and len(data) >= 6:
            rn = int(data[3])
            pulse = int(data[4]) | (int(data[5]) << 8)
            rec.runtime.relay_pulse_ms[rn] = pulse
            st = rec.runtime.relays.setdefault(rn, RelayState(relay_no=rn))
            st.pulse_ms = pulse
        elif cmd == COMMAND_SET_RELAY_STATE and len(data) >= 5:
            self.store_relay_state(rec.module_id, int(data[3]), bool(int(data[4])))
            return
        elif cmd == COMMAND_GET_BUTTON_TIMING and len(data) >= 5:
            rec.runtime.button_timing = {
                "multiclick_ms": int(data[3]) * 10,
                "longpress_ms": int(data[4]) * 10,
            }
        self._notify()

    def send_config_and_wait(
        self,
        module_id: int,
        command: int,
        args: list[int] | None = None,
        *,
        timeout: float = 1.0,
    ) -> list[int] | None:
        return self._get_engine().send_request(
            int(module_id), int(command), args, timeout=timeout, log_traffic=False
        )

    def store_gpio_roles(self, module_id: int, roles: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.gpio_roles = dict(roles)
            for role_info in roles.values():
                if int(role_info.get("role", 0)) != RELAY_GPIO_ROLE:
                    continue
                idx = int(role_info.get("index", 0))
                gpio = int(role_info.get("gpio", 0))
                if idx > 0 and gpio > 0:
                    rec.runtime.relay_gpio_map[idx] = gpio
            self._notify()

    def store_gpio_values(self, module_id: int, values: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.gpio_values = dict(values)
            self._notify()

    def update_gpio_role(self, module_id: int, gpio: int, row: dict[str, Any]) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.gpio_roles[str(int(gpio))] = row
            self._notify()

    def clear_gpio_roles(self, module_id: int) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.gpio_roles.clear()
            rec.runtime.gpio_values.clear()
            self._notify()

    def store_relay_pulse(self, module_id: int, relay_no: int, pulse_ms: int) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.relay_pulse_ms[int(relay_no)] = int(pulse_ms)
            st = rec.runtime.relays.setdefault(int(relay_no), RelayState(relay_no=int(relay_no)))
            st.pulse_ms = int(pulse_ms)
            self._notify()

    def store_relay_state(self, module_id: int, relay_no: int, is_on: bool) -> None:
        """Aktualizuj cache stanu przekaźnika (odpowiedź SET_RELAY / GPIO / broadcast 0x600)."""
        with self._lock:
            rec = self._get_module(int(module_id))
            if rec is None:
                return
            rn = int(relay_no)
            st = rec.runtime.relays.setdefault(rn, RelayState(relay_no=rn))
            st.on = bool(is_on)
            if rn >= MCP23017_RELAY_CAN_BASE:
                st.source = "mcp23017"
            else:
                st.source = "local"
            self._notify()

    def store_button_timing(self, module_id: int, multiclick_ms: int, longpress_ms: int) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.button_timing = {
                "multiclick_ms": int(multiclick_ms),
                "longpress_ms": int(longpress_ms),
            }
            self._notify()

    def store_mappings(self, module_id: int, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.mappings = list(rows)
            self._notify()

    def clear_sensors(self, module_id: int) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.sensors.clear()
            self._notify()

    @staticmethod
    def _ascii_from_bytes(data: list[int]) -> str:
        out: list[str] = []
        for b in data:
            if b == 0:
                break
            if 0x20 <= b < 0x7F:
                out.append(chr(b))
        return "".join(out)

    @staticmethod
    def _build_summary_details(raw: list[int]) -> str:
        if len(raw) < 8:
            return ""
        return f"buttons={raw[3]} relays={raw[4]} ds18={raw[5]} shutters={raw[6]}"

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            if self._bus is None:
                time.sleep(0.3)
                continue
            if not self._rx_enabled.is_set():
                time.sleep(0.05)
                continue
            message = self._recv(0.1)
            if message is None:
                continue
            with self._lock:
                self._handle_message(message)

    def pump_rx(self, timeout: float = 0.05) -> None:
        if self._bus is None:
            return
        msg = self._recv(timeout)
        if msg is None:
            return
        with self._lock:
            self._handle_message(msg)

    def _drain_rx(self) -> None:
        if self._bus is None:
            return
        while True:
            msg = self._recv(0.02)
            if msg is None:
                break
            with self._lock:
                self._handle_message(msg)

    def send_config(self, module_id: int, command: int, args: list[int] | None = None) -> bool:
        payload = [int(module_id), int(command), 0, 0, 0, 0, 0, 0]
        if args:
            for i, val in enumerate(args[:6]):
                payload[2 + i] = int(val) & 0xFF
        return self.send_raw(can_v2_config_request_id(int(module_id)), payload)

    def send_raw(self, can_id: int, data: list[int]) -> bool:
        if not self.ensure_bus():
            return False
        module_id = int(data[0]) if data else 0xFF
        self._sync_transport_macs_from_engine()
        module_has_key: bool | None = None
        if module_id not in UNKNOWN_MODULE_IDS:
            module_has_key = self._get_engine()._module_has_master_key_for_tx(module_id)  # noqa: SLF001
        frames = prepare_outgoing_frames(
            self._transport,
            module_id,
            can_id,
            data,
            module_has_master_key=module_has_key,
            secure_can=self._options.secure_can,
        )
        if not frames:
            _LOGGER.warning("TX blocked can_id=0x%X module=%s", can_id, module_id)
            return False
        import can

        for frame_id, frame_data in frames:
            ok = self._send_message(
                can.Message(
                    arbitration_id=int(frame_id),
                    is_extended_id=False,
                    data=frame_data,
                )
            )
            if not ok:
                return False
        return True

    def send_ota_data(self, module_id: int, data: list[int]) -> bool:
        """Send OTA_DATA frame (V3 frame_type 3) — payload is seq + firmware bytes."""
        if not self.ensure_bus():
            return False
        self._sync_transport_macs_from_engine()
        module_has_key = self._get_engine()._module_has_master_key_for_tx(int(module_id))  # noqa: SLF001
        frames = prepare_outgoing_frames(
            self._transport,
            int(module_id),
            can_v2_ota_data_id(int(module_id)),
            data,
            module_has_master_key=module_has_key,
            secure_can=self._options.secure_can,
        )
        if not frames:
            return False
        import can

        for frame_id, frame_data in frames:
            if not self._send_message(
                can.Message(arbitration_id=int(frame_id), is_extended_id=False, data=frame_data)
            ):
                return False
        return True

    def set_relay_state(self, module_id: int, relay_no: int, state: str) -> dict[str, Any]:
        if not self.ensure_bus():
            return {"ok": False, "error": self._bus_error or "bus not open"}
        return self._get_engine().set_relay_state(int(module_id), int(relay_no), state)

    def set_shutter_command(
        self,
        module_id: int,
        shutter_no: int,
        command: str,
        param: int = 0,
    ) -> dict[str, Any]:
        if not self.ensure_bus():
            return {"ok": False, "error": self._bus_error or "bus not open"}
        return self._get_engine().set_shutter_command(module_id, shutter_no, command, param)

    def auto_scan_broadcast(self) -> bool:
        return self.send_config(0xFF, COMMAND_GET_SUMMARY)

    def refresh_module(self, module_id: int) -> dict[str, Any]:
        if not self.ensure_bus():
            return {"ok": False, "error": self._bus_error or "bus not open"}
        if not self._begin_exclusive_io():
            return {"ok": False, "error": "bus busy (scan/refresh in progress)"}
        try:
            result = _refresh_module_deep_impl(self, module_id)
            if result.get("ok"):
                self.persist_discovery_state()
            return result
        finally:
            self._end_exclusive_io()

    def reboot_module(self, module_id: int) -> dict[str, Any]:
        mid = int(module_id)
        if not (1 <= mid <= 255):
            return {"ok": False, "error": "invalid module_id"}
        ok = self.send_config(mid, COMMAND_REBOOT_MODULE)
        return {"ok": ok, "module_id": mid}

    def discovery_scan(self) -> dict[str, Any]:
        if not self.ensure_bus():
            self._last_scan_status = "error"
            return {"ok": False, "error": self._bus_error or "bus not open"}
        try:
            result = self._get_engine().scan_modules_sync()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("discovery_scan failed: %s", err, exc_info=True)
            self._last_scan_status = "error"
            return {"ok": False, "error": str(err)}
        self._sync_transport_macs_from_engine()
        scan_at: float | None = None
        if result.get("ok"):
            try:
                self.refresh_relay_telemetry(0.8)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Post-scan relay telemetry failed", exc_info=True)
            if self._options.master_key_bytes is not None:
                for rec in self._get_engine().list_modules():
                    mid = int(rec["module_id"])
                    try:
                        _refresh_module_deep_impl(self, mid)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Deep refresh module %s failed", mid, exc_info=True)
            scan_at = time.time()
            try:
                self.persist_discovery_state(last_scan_at=scan_at)
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Could not persist discovery state", exc_info=True)
        with self._lock:
            self._last_scan_status = "ok" if result.get("ok") else "error"
            if result.get("ok"):
                self._last_scan_at = scan_at or time.time()
        modules = self.list_modules()
        result["module_count"] = len(modules)
        result["modules"] = modules
        store = discovery_snapshot(scan_status=self._last_scan_status)
        result["discovery_version"] = store.get("discovery_version")
        return result
