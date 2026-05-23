from __future__ import annotations

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
        self._bus = None
        self._transport: SecureCanTransport | None = None
        self._modules: dict[int, ModuleRecord] = {}
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None
        self._bus_error: str | None = None
        self._last_scan_status: str = "never"
        self._last_scan_at: float | None = None
        self._frame_listeners: list[Callable[[], None]] = []

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

    def stop(self) -> None:
        self._stop.set()
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
                "can_port": self._options.can_port,
                "gsusb_channel": self._options.gsusb_channel,
                "can_bitrate": self._options.can_bitrate,
                "secure_enabled": self._transport is not None,
                "module_count": len(self._modules),
                "last_scan_status": self._last_scan_status,
                "last_scan_at": self._last_scan_at,
                "version": "0.3.0",
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

        port = self._options.can_port
        last_err: Exception | None = None
        candidates: list[int] = []
        for baud in (self._options.tty_baudrate, *SERIAL_BAUD_CANDIDATES):
            if baud not in candidates:
                candidates.append(baud)
        for serial_baud in candidates:
            try:
                self._bus = can.Bus(
                    interface="slcan",
                    channel=port,
                    bitrate=bitrate,
                    ttyBaudrate=serial_baud,
                )
                self._bus_error = None
                _LOGGER.info("SLCAN open %s @ %d (CAN %d)", port, serial_baud, bitrate)
                return
            except Exception as err:  # noqa: BLE001
                last_err = err
        self._bus_error = f"Cannot open SLCAN on {port}: {last_err}"
        _LOGGER.error(self._bus_error)

    def _close_bus(self) -> None:
        try:
            if self._bus is not None:
                self._bus.shutdown()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("bus shutdown error", exc_info=True)
        finally:
            self._bus = None

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
            rec.runtime.hw_flags = int(data[7]) if len(data) > 7 else 0
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
            try:
                message = self._bus.recv(timeout=min(0.05, max(0.0, deadline - time.time())))
            except Exception:  # noqa: BLE001
                continue
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
            try:
                message = self._bus.recv(timeout=0.1)
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
                continue
            if message is None:
                continue
            with self._lock:
                self._handle_message(message)

    def pump_rx(self, timeout: float = 0.05) -> None:
        if self._bus is None:
            return
        msg = self._bus.recv(timeout=timeout)
        if msg is None:
            return
        with self._lock:
            self._handle_message(msg)

    def _drain_rx(self) -> None:
        if self._bus is None:
            return
        while True:
            msg = self._bus.recv(timeout=0.02)
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
        if self._bus is None:
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
            self._bus.send(
                can.Message(
                    arbitration_id=int(frame_id),
                    is_extended_id=True,
                    data=frame_data,
                )
            )
        return True

    def set_relay_state(self, module_id: int, relay_no: int, state: str) -> dict[str, Any]:
        state_map = {"on": 1, "off": 0, "toggle": 2}
        code = state_map.get(str(state).lower())
        if code is None:
            return {"ok": False, "error": "invalid state"}
        ok = self.send_config(int(module_id), COMMAND_SET_RELAY_STATE, [int(relay_no), code])
        if ok:
            deadline = time.time() + 1.0
            while time.time() < deadline:
                self.pump_rx(0.05)
        return {"ok": ok, "module_id": module_id, "relay_no": relay_no, "state": state}

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
        return _refresh_module_deep_impl(self, module_id)

    def reboot_module(self, module_id: int) -> dict[str, Any]:
        mid = int(module_id)
        if not (1 <= mid <= 255):
            return {"ok": False, "error": "invalid module_id"}
        ok = self.send_config(mid, COMMAND_REBOOT_MODULE)
        return {"ok": ok, "module_id": mid}

    def discovery_scan(self) -> dict[str, Any]:
        if self._bus is None:
            self._last_scan_status = "error"
            return {"ok": False, "error": self._bus_error or "bus not open"}

        with self._lock:
            before = len(self._modules)
            self._drain_rx()
            for attempt in range(_BROADCAST_ATTEMPTS):
                self.send_config(0xFF, COMMAND_GET_SUMMARY)
                if attempt < _BROADCAST_ATTEMPTS - 1:
                    time.sleep(_BROADCAST_GAP_S)

        deadline = time.time() + _PASSIVE_LISTEN_S
        while time.time() < deadline:
            self.pump_rx(min(0.05, max(0.0, deadline - time.time())))

        with self._lock:
            for module_id in sorted(self._modules.keys()):
                self.send_config(module_id, COMMAND_GET_SUMMARY)
                time.sleep(0.15)
                self.send_config(module_id, COMMAND_GET_MODULE_NAME)
                time.sleep(_NAME_READ_GAP_S)
                self.send_config(module_id, COMMAND_GET_BUILD_INFO)
                time.sleep(_NAME_READ_GAP_S)
                dl = time.time() + 0.6
                while time.time() < dl:
                    self.pump_rx(0.05)

            count = len(self._modules)
            self._last_scan_status = "ok"
            self._last_scan_at = time.time()

        _LOGGER.info("Discovery scan finished: modules=%d (was %d)", count, before)
        return {"ok": True, "modules_before": before, "modules_after": count, "modules": self.list_modules()}
