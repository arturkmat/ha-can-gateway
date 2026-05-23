from __future__ import annotations

import glob
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from can_secure_transport import SecureCanTransport
from protocol_constants import (
    CAN_ID_CONFIG_REQUEST,
    CAN_ID_CONFIG_RESPONSE,
    CAN_ID_DEVICE_INFO,
    CAN_ID_RELAY_GPIO_MAP,
    CAN_ID_RELAYS,
    CAN_ID_RELAYS_MCP23017,
    CAN_ID_SENSORS,
    CAN_ID_SHUTTER_CMD,
    CAN_ID_SHUTTER_STATUS,
    COMMAND_GET_BUILD_INFO,
    COMMAND_GET_BUTTON_TIMING,
    COMMAND_GET_MCP23017_ROLE_DUMP,
    COMMAND_GET_MODULE_NAME,
    COMMAND_GET_RELAY_PULSE,
    COMMAND_GET_SHUTTER_RELAYS,
    COMMAND_GET_SUMMARY,
    COMMAND_REBOOT_MODULE,
    COMMAND_SET_RELAY_STATE,
    HW_TYPE_NAME_MAP,
    MCP23017_RELAY_CAN_BASE,
    SHIFT595_RELAY_BASE_INDEX,
    SHIFT595_RELAY_COUNT_PER_REGISTER,
    UNKNOWN_MODULE_IDS,
)

from .can_send import prepare_outgoing_frames
from .deep_config import refresh_module_deep as _refresh_module_deep_impl
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
_BROADCAST_ATTEMPTS = 3
_BROADCAST_GAP_S = 0.08
_PASSIVE_LISTEN_S = 4.5
_NAME_READ_GAP_S = 0.25
MAX_LOCAL_RELAYS = 16

SHUTTER_CMD_MAP = {"open": 1, "close": 2, "stop": 3, "position": 4}


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

    def start(self) -> None:
        master = self._options.master_key_bytes
        if master is not None:
            self._transport = SecureCanTransport(master_key=master)
            _LOGGER.info("Secure CAN enabled (MASTER_KEY %d bytes)", len(master))
        elif self._options.master_key_hex.strip():
            _LOGGER.error("Niepoprawny MASTER_KEY — wymagane 64 znaki hex")
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
            return {
                "bus_ok": self.bus_ok,
                "bus_error": self._bus_error,
                "can_interface": self._options.can_interface,
                "can_port": self._active_port or self._options.can_port,
                "configured_port": self._options.can_port,
                "gsusb_channel": self._options.gsusb_channel,
                "can_bitrate": self._options.can_bitrate,
                "secure_enabled": self._transport is not None,
                "module_count": len(self._modules),
                "last_scan_status": self._last_scan_status,
                "last_scan_at": self._last_scan_at,
                "version": "0.3.11",
                "mqtt_enabled": self._options.mqtt_enabled,
            }

    def full_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self.status(),
                "modules": [
                    rec.to_dict(include_runtime=True)
                    for rec in sorted(self._modules.values(), key=lambda r: r.module_id)
                ],
            }

    def list_modules(self) -> list[dict[str, Any]]:
        with self._lock:
            return [rec.to_dict() for rec in sorted(self._modules.values(), key=lambda r: r.module_id)]

    def module_detail(self, module_id: int) -> dict[str, Any] | None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return None
            return rec.to_dict(include_runtime=True)

    def relay_numbers_for_module(self, module_id: int) -> set[int]:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return set()
            nums: set[int] = set()
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

    def _open_bus(self) -> None:
        import can

        iface = self._options.can_interface
        bitrate = self._options.can_bitrate
        if iface == CAN_INTERFACE_GS_USB:
            try:
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
        message = self._normalize_message(message)
        if message is None or not message.data:
            return
        data = list(message.data)
        module_id = int(data[0])
        rec = self._get_module(module_id)
        if rec is None:
            return
        fid = message.arbitration_id

        if fid == CAN_ID_DEVICE_INFO and len(data) >= 8:
            mac = ":".join(f"{b:02X}" for b in data[2:8])
            hw = int(data[1])
            self._register_mac(module_id, mac)
            rec.hw_type = hw
            rec.hw_name = HW_TYPE_NAME_MAP.get(hw, "unknown")
            rec.mac = mac
            self._notify()
            return

        if fid == CAN_ID_CONFIG_RESPONSE and len(data) >= 3:
            self._handle_config_response(rec, data)
            return

        if fid == CAN_ID_RELAYS:
            for rn, is_on in decode_relays_0x600(data):
                st = rec.runtime.relays.setdefault(rn, RelayState(relay_no=rn))
                st.on = is_on
                st.source = "local"
            self._notify()
            return

        if fid == CAN_ID_RELAYS_MCP23017:
            for rn, is_on in decode_mcp_relays_0x602(data):
                st = rec.runtime.relays.setdefault(rn, RelayState(relay_no=rn))
                st.on = is_on
                st.source = "mcp23017"
            self._notify()
            return

        if fid == CAN_ID_RELAY_GPIO_MAP and len(data) >= 3:
            start = int(data[1])
            for i, gpio in enumerate(data[2:]):
                rec.runtime.relay_gpio_map[start + i] = int(gpio)
            self._notify()
            return

        if fid == CAN_ID_SHUTTER_STATUS and len(data) >= 4:
            sid = int(data[1])
            sh = rec.runtime.shutters.setdefault(sid, ShutterState(shutter_no=sid))
            sh.position = int(data[2])
            sh.direction = int(data[3])
            ro, rc = rec.runtime.shutter_map.get(sid, (0, 0))
            sh.relay_open, sh.relay_close = ro, rc
            self._notify()
            return

        if fid == CAN_ID_SENSORS and len(data) >= 7:
            rec.runtime.sensors.append(
                {
                    "sensor_no": int(data[1]),
                    "sensor_type": int(data[2]),
                    "data": data[3:],
                    "ts": time.time(),
                }
            )
            if len(rec.runtime.sensors) > 32:
                rec.runtime.sensors = rec.runtime.sensors[-32:]
            self._notify()

    def _handle_config_response(self, rec: ModuleRecord, data: list[int]) -> None:
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
        if not self.send_config(module_id, command, args):
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._bus is None:
                return None
            message = self._recv(min(0.05, max(0.0, deadline - time.time())))
            if message is None:
                continue
            with self._lock:
                message = self._normalize_message(message)
                if message is None:
                    continue
                self._handle_message(message)
                if message.arbitration_id != CAN_ID_CONFIG_RESPONSE:
                    continue
                data = list(message.data)
                if len(data) < 3:
                    continue
                if int(data[1]) != int(command):
                    continue
                if int(data[0]) != int(module_id):
                    continue
                return data
        return None

    def store_gpio_roles(self, module_id: int, roles: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            rec = self._modules.get(int(module_id))
            if rec is None:
                return
            rec.runtime.gpio_roles = dict(roles)
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
        return self.send_raw(CAN_ID_CONFIG_REQUEST, payload)

    def send_raw(self, can_id: int, data: list[int]) -> bool:
        if not self.ensure_bus():
            return False
        module_id = int(data[0]) if data else 0xFF
        with self._lock:
            if self._transport is not None:
                for mid, rec in self._modules.items():
                    if rec.mac:
                        self._register_mac(mid, rec.mac)
            frames = prepare_outgoing_frames(self._transport, module_id, can_id, data)
        if not frames:
            _LOGGER.warning("TX blocked can_id=0x%X module=%s", can_id, module_id)
            return False
        import can

        for frame_id, frame_data in frames:
            ok = self._send_message(
                can.Message(
                    arbitration_id=int(frame_id),
                    is_extended_id=True,
                    data=frame_data,
                )
            )
            if not ok:
                return False
        return True

    def set_relay_state(self, module_id: int, relay_no: int, state: str) -> dict[str, Any]:
        if not self.ensure_bus():
            return {"ok": False, "error": self._bus_error or "bus not open"}
        state_map = {"on": 1, "off": 0, "toggle": 2}
        code = state_map.get(str(state).lower())
        if code is None:
            return {"ok": False, "error": "invalid state"}
        mid = int(module_id)
        rn = int(relay_no)
        resp = self.send_config_and_wait(mid, COMMAND_SET_RELAY_STATE, [rn, code], timeout=0.6)
        if resp is not None and len(resp) >= 5 and int(resp[2]) == 0:
            self.store_relay_state(mid, rn, bool(int(resp[4])))
        elif resp is not None and len(resp) >= 3 and int(resp[2]) != 0:
            return {
                "ok": False,
                "error": f"status={int(resp[2])}",
                "module_id": mid,
                "relay_no": rn,
            }
        else:
            deadline = time.time() + 0.6
            while time.time() < deadline:
                self.pump_rx(0.05)
            detail = self.module_detail(mid)
            relays = (detail or {}).get("runtime", {}).get("relays") or []
            cached = next((r for r in relays if int(r.get("relay_no", -1)) == rn), None)
            if cached is None:
                return {"ok": False, "error": "no response", "module_id": mid, "relay_no": rn}
        deadline = time.time() + 0.5
        while time.time() < deadline:
            self.pump_rx(0.05)
        detail = self.module_detail(mid)
        relays = (detail or {}).get("runtime", {}).get("relays") or []
        row = next((r for r in relays if int(r.get("relay_no", -1)) == rn), None)
        is_on = bool(row.get("on")) if row else None
        return {
            "ok": True,
            "module_id": mid,
            "relay_no": rn,
            "state": state,
            "on": is_on,
        }

    def set_shutter_command(
        self,
        module_id: int,
        shutter_no: int,
        command: str,
        param: int = 0,
    ) -> dict[str, Any]:
        cmd = SHUTTER_CMD_MAP.get(str(command).lower())
        if cmd is None:
            return {"ok": False, "error": "invalid command"}
        target = max(0, min(100, int(param)))
        param_byte = target if cmd == 4 else 0
        payload = [int(module_id), int(shutter_no), cmd, param_byte, 0, 0, 0, 0]
        ok = self.send_raw(CAN_ID_SHUTTER_CMD, payload)
        if ok:
            deadline = time.time() + 2.0
            while time.time() < deadline:
                self.pump_rx(0.05)
        return {"ok": ok, "module_id": module_id, "shutter_no": shutter_no, "command": command}

    def auto_scan_broadcast(self) -> bool:
        return self.send_config(0xFF, COMMAND_GET_SUMMARY)

    def refresh_module(self, module_id: int) -> dict[str, Any]:
        if not self.ensure_bus():
            return {"ok": False, "error": self._bus_error or "bus not open"}
        if not self._begin_exclusive_io():
            return {"ok": False, "error": "bus busy (scan/refresh in progress)"}
        try:
            return _refresh_module_deep_impl(self, module_id)
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
        if not self._begin_exclusive_io():
            return {"ok": False, "error": "scan already in progress"}

        try:
            with self._lock:
                before = len(self._modules)
            self._drain_rx()
            for attempt in range(_BROADCAST_ATTEMPTS):
                self.send_config(0xFF, COMMAND_GET_SUMMARY)
                if attempt < _BROADCAST_ATTEMPTS - 1:
                    time.sleep(_BROADCAST_GAP_S)

            deadline = time.time() + _PASSIVE_LISTEN_S
            while time.time() < deadline:
                if self._bus is None and not self.ensure_bus():
                    raise RuntimeError(self._bus_error or "bus closed during scan")
                self.pump_rx(min(0.05, max(0.0, deadline - time.time())))

            module_ids = sorted(self._modules.keys())
            for module_id in module_ids:
                self.send_config(module_id, COMMAND_GET_SUMMARY)
                time.sleep(0.15)
                self.send_config(module_id, COMMAND_GET_MODULE_NAME)
                time.sleep(_NAME_READ_GAP_S)
                self.send_config(module_id, COMMAND_GET_BUILD_INFO)
                time.sleep(_NAME_READ_GAP_S)
                dl = time.time() + 0.6
                while time.time() < dl:
                    if self._bus is None and not self.ensure_bus():
                        raise RuntimeError(self._bus_error or "bus closed during scan")
                    self.pump_rx(0.05)

            with self._lock:
                count = len(self._modules)
                self._last_scan_status = "ok"
                self._last_scan_at = time.time()

            _LOGGER.info("Discovery scan finished: modules=%d (was %d)", count, before)
            return {"ok": True, "modules_before": before, "modules_after": count, "modules": self.list_modules()}
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Discovery scan failed: %s", err, exc_info=True)
            self._last_scan_status = "error"
            return {"ok": False, "error": str(err)}
        finally:
            self._end_exclusive_io()
