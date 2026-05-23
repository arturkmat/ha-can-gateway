"""Headless CAN configurator engine — ta sama logika protokołu co UsbCanConfigurator (app.py).

Używany przez konfigurator Windows (docelowo) oraz dodatek Home Assistant CAN Gateway.
Bez Tkinter / messagebox — tylko magistrala CAN i cache stanu modułów.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Protocol

from can_secure_transport import is_plaintext_bootstrap_tx
from pinout_data import DEVICE_PINOUTS
from protocol_constants import (
    CAN_ID_CONFIG_REQUEST,
    CAN_ID_CONFIG_RESPONSE,
    CAN_ID_DEVICE_INFO,
    CAN_ID_RELAY_GPIO_MAP,
    CAN_ID_RELAYS,
    CAN_ID_RELAYS_MCP23017,
    CAN_ID_SENSORS,
    CAN_ID_SHUTTER_STATUS,
    COMMAND_GET_BUILD_INFO,
    COMMAND_GET_BUTTON_TIMING,
    COMMAND_GET_GPIO_ROLE,
    COMMAND_GET_GPIO_VALUE,
    COMMAND_GET_MCP23017_ROLE_DUMP,
    COMMAND_GET_MODULE_NAME,
    COMMAND_GET_RELAY_PULSE,
    COMMAND_GET_SHIFT595_FLAGS,
    COMMAND_GET_SHUTTER_RELAYS,
    COMMAND_GET_SUMMARY,
    COMMAND_PROVISION_GET_MASTER_KEY_STATE,
    COMMAND_SCAN_MCP23017,
    COMMAND_SET_MODULE_ID,
    COMMAND_SET_MODULE_ID_BY_MAC,
    COMMAND_SET_RELAY_STATE,
    HW_TYPE_NAME_MAP,
    HW_TYPE_OTHER,
    HW_TYPE_TO_PINOUT,
    MAX_SHUTTERS,
    MCP23017_OUTPUT_COUNT,
    MCP23017_PIN_ROLE_RELAY,
    MCP23017_RELAY_BASE_INDEX,
    MCP23017_RELAY_BASE_INDEX,
    PIN_ROLE_MAP,
    PLAINTEXT_TELEMETRY_CAN_IDS,
    SHIFT595_RELAY_BASE_INDEX,
    SHIFT595_RELAY_COUNT_PER_REGISTER,
    UNKNOWN_MODULE_IDS,
)

_LOGGER = logging.getLogger(__name__)

SHUTTER_DIRECTION_TEXT = {0: "stopped", 1: "opening", 2: "closing"}
ROLE_NAME_BY_CODE = {v: k for k, v in PIN_ROLE_MAP.items()}
TAB_MODULES = 1
TAB_GPIO = 2
TAB_CONTROL = 3
TAB_SHUTTERS = 4
TAB_MAPPING = 5
TAB_SENSORS = 6
TAB_NAME_TO_INDEX = {
    "modules": TAB_MODULES,
    "gpio": TAB_GPIO,
    "control": TAB_CONTROL,
    "shutters": TAB_SHUTTERS,
    "mapping": TAB_MAPPING,
    "sensors": TAB_SENSORS,
}


class CanIoBackend(Protocol):
    def recv(self, timeout: float) -> Any | None: ...
    def bus_send(self, target_module_id: int, can_id: int, data: list[int], *, log_traffic: bool = True) -> None: ...
    def normalize(self, message: Any) -> Any | None: ...
    def io_acquire(self) -> None: ...
    def io_release(self) -> None: ...
    def log(self, message: str) -> None: ...
    def notify(self) -> None: ...
    def bus_ok(self) -> bool: ...


@dataclass
class ModuleContext:
    module_id: int
    hw_type: int = HW_TYPE_OTHER
    module_mac: int | None = None
    name: str | None = None
    firmware_build: str | None = None
    summary_details: str = ""
    button_count: int | None = None
    relay_count: int | None = None
    shutter_count: int | None = None
    hw_flags: int = 0
    has_master_key: bool | None = None
    last_seen_s: float = 0.0
    last_summary_response: list[int] | None = None
    gpio_info: dict[int, dict[str, Any]] = field(default_factory=dict)
    gpio_values: dict[int, dict[str, Any]] = field(default_factory=dict)
    virtual_relay_values: dict[int, int] = field(default_factory=dict)
    relay_gpio_by_index: dict[int, int] = field(default_factory=dict)
    relay_pulse_ms_by_index: dict[int, int] = field(default_factory=dict)
    shutter_relay_pairs: dict[int, dict[str, int]] = field(default_factory=dict)
    shutter_states: dict[int, dict[str, int]] = field(default_factory=dict)
    mcp_relay_pins: dict[int, set[int]] = field(default_factory=dict)
    mcp23017_found_mask: int = 0
    shift595_q_flags: dict[int, int] = field(default_factory=dict)
    mappings: list[dict[str, Any]] = field(default_factory=list)
    sensors: list[dict[str, Any]] = field(default_factory=list)
    button_timing: dict[str, int] = field(default_factory=dict)


class ConfiguratorEngine:
    """Headless silnik konfiguratora — send_request, skan F5, lazy-load zakładek."""

    def __init__(
        self,
        io: CanIoBackend,
        *,
        master_key: bytes | None = None,
        read_mappings: Callable[[ConfiguratorEngine, int], dict[str, Any]] | None = None,
    ) -> None:
        self._io = io
        self._master_key = master_key
        self._read_mappings = read_mappings
        self._io_lock = threading.RLock()
        self._io_depth = 0
        self.discovered_modules: list[dict[str, Any]] = []
        self._module_key_mismatch: set[int] = set()
        self._contexts: dict[int, ModuleContext] = {}
        self.current_module_id: int = 1
        self._last_scan_status = "never"
        self._last_scan_at: float | None = None

    # --- public API ---

    def set_current_module(self, module_id: int) -> None:
        self.current_module_id = int(module_id)

    def context(self, module_id: int | None = None) -> ModuleContext:
        mid = int(module_id if module_id is not None else self.current_module_id)
        ctx = self._contexts.get(mid)
        if ctx is None:
            ctx = ModuleContext(module_id=mid)
            self._contexts[mid] = ctx
        return ctx

    def scan_modules_sync(self) -> dict[str, Any]:
        if not self._io.bus_ok():
            self._last_scan_status = "error"
            return {"ok": False, "error": "bus not open"}
        self._io_acquire()
        try:
            self._io.invalidate_transport_macs()
            before = len(self.discovered_modules)
            self._module_key_mismatch.clear()
            self.discovered_modules = []
            self._drain_rx()
            payload = [0xFF, COMMAND_GET_SUMMARY, 0, 0, 0, 0, 0, 0]
            for attempt in range(3):
                self._secure_bus_send(0xFF, CAN_ID_CONFIG_REQUEST, payload, log_traffic=False)
                if attempt < 2:
                    time.sleep(0.08)
            deadline = time.time() + 4.5
            pending_summary_macs: list[int] = []
            touched_ids: set[int] = set()
            while time.time() < deadline:
                message = self._safe_recv(min(0.02, max(0.0, deadline - time.time())))
                if message is None:
                    continue
                message = self._normalize(message)
                if message is None:
                    continue
                raw = list(message.data)
                if message.arbitration_id == CAN_ID_DEVICE_INFO and len(raw) >= 8:
                    module_id = raw[0]
                    hw_type = raw[1]
                    module_mac = self._extract_device_mac(raw)
                    if module_mac is not None:
                        pending_summary_macs.append(module_mac)
                    if self._upsert_discovered(module_id, hw_type=hw_type, module_mac=module_mac):
                        self._io.notify()
                    if module_mac is not None:
                        self._io.sync_transport_macs()
                    if module_id not in UNKNOWN_MODULE_IDS:
                        touched_ids.add(module_id)
                    continue
                if (
                    message.arbitration_id == CAN_ID_CONFIG_RESPONSE
                    and len(raw) >= 8
                    and raw[1] == COMMAND_GET_SUMMARY
                    and raw[2] == 0
                ):
                    module_id = raw[0]
                    details = self.build_summary_details(raw)
                    if module_id in UNKNOWN_MODULE_IDS:
                        if pending_summary_macs:
                            summary_mac = pending_summary_macs.pop(0)
                            self._upsert_discovered(module_id, details=details, module_mac=summary_mac)
                            self._io.sync_transport_macs()
                        continue
                    if self._upsert_discovered(module_id, details=details):
                        self._io.notify()
                    if len(raw) > 7:
                        self.context(module_id).hw_flags = int(raw[7])
                        self._apply_summary_counts(module_id, raw)
                    touched_ids.add(module_id)
                    continue
                self.handle_can_message(message, already_normalized=True)

            for module_id in sorted(touched_ids):
                if module_id in UNKNOWN_MODULE_IDS:
                    continue
                self._sync_module_master_key_state(module_id)
                name = self._read_module_name(module_id)
                if name:
                    for item in self.discovered_modules:
                        if item.get("module_id") == module_id:
                            item["name"] = name
                            break
                build = self._read_module_build(module_id)
                if build:
                    self.context(module_id).firmware_build = build

            self._io.sync_transport_macs()
            if touched_ids:
                self.refresh_all_module_relay_states(passive_timeout_s=1.5, active=True)
            count = len(self.discovered_modules)
            self._last_scan_status = "ok"
            self._last_scan_at = time.time()
            self._io.notify()
            return {"ok": True, "modules_before": before, "modules_after": count, "modules": self.list_modules()}
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("scan failed: %s", err, exc_info=True)
            self._last_scan_status = "error"
            return {"ok": False, "error": str(err)}
        finally:
            self._io_release()

    def run_tab_load(self, module_id: int, tab: str | int) -> dict[str, Any]:
        tab_index = TAB_NAME_TO_INDEX.get(str(tab).lower()) if isinstance(tab, str) else int(tab)
        if tab_index is None:
            return {"ok": False, "error": f"unknown tab: {tab}"}
        mid = int(module_id)
        if not (1 <= mid <= 254):
            return {"ok": False, "error": "invalid module_id"}
        if not self._io.bus_ok():
            return {"ok": False, "error": "bus not open"}
        self.set_current_module(mid)
        steps_def = self.build_tab_load_steps(mid, tab_index)
        steps: list[dict[str, Any]] = []
        self._io_acquire()
        try:
            for name, fn in steps_def:
                try:
                    fn()
                    steps.append({"name": name, "ok": True})
                except Exception as err:  # noqa: BLE001
                    steps.append({"name": name, "ok": False, "error": str(err)})
            self._io.notify()
        finally:
            self._io_release()
        ok = all(s.get("ok", False) for s in steps)
        out: dict[str, Any] = {"ok": ok, "tab": str(tab).lower() if isinstance(tab, str) else tab_index, "steps": steps}
        detail = self.export_module_dict(mid)
        if tab_index == TAB_MODULES:
            out["module"] = detail
        elif tab_index == TAB_GPIO:
            out["button_timing"] = detail.get("runtime", {}).get("button_timing")
        elif tab_index == TAB_CONTROL:
            out["control_relays"] = detail.get("control_relays") or []
        elif tab_index == TAB_SHUTTERS:
            out["shutter_count"] = detail.get("shutter_count")
        elif tab_index == TAB_MAPPING:
            out["mappings"] = detail.get("runtime", {}).get("mappings") or []
        elif tab_index == TAB_SENSORS:
            out["sensors"] = detail.get("runtime", {}).get("sensors") or []
        return out

    def handle_can_message(
        self, message: Any, *, passive_only: bool = False, already_normalized: bool = False
    ) -> bool:
        if not already_normalized:
            message = self._normalize(message)
        if message is None or not message.data:
            return False
        payload = list(message.data)
        module_id = int(payload[0])

        if message.arbitration_id == CAN_ID_DEVICE_INFO and len(payload) >= 8:
            hw = payload[1]
            mac = self._extract_device_mac(payload)
            if self._upsert_discovered(module_id, hw_type=hw, module_mac=mac):
                self._io.notify()
            return True

        if passive_only:
            return False

        ctx = self.context(module_id)
        if message.arbitration_id == CAN_ID_RELAYS:
            return self._apply_relays_frame(ctx, payload)
        if message.arbitration_id == CAN_ID_RELAYS_MCP23017:
            return self._apply_mcp_relays_frame(ctx, payload)
        if message.arbitration_id == CAN_ID_SHUTTER_STATUS and len(payload) >= 4:
            sid = int(payload[1])
            if sid < 1 or sid > MAX_SHUTTERS:
                return False
            pos = int(payload[2])
            direction = int(payload[3])
            ctx.shutter_relay_pairs.setdefault(sid, {})
            prev = ctx.shutter_states.get(sid)
            new_state = {"position": pos, "direction": direction}
            ctx.shutter_states[sid] = new_state
            if prev != new_state:
                self._io.notify()
            return True
        if message.arbitration_id == CAN_ID_SENSORS and len(payload) >= 7:
            ctx.sensors.append(
                {"sensor_no": int(payload[1]), "sensor_type": int(payload[2]), "data": payload[3:], "ts": time.time()}
            )
            if len(ctx.sensors) > 32:
                ctx.sensors = ctx.sensors[-32:]
            self._io.notify()
            return True
        if message.arbitration_id == CAN_ID_RELAY_GPIO_MAP and len(payload) >= 3:
            return self._apply_relay_gpio_map_frame(ctx, payload)
        if message.arbitration_id == CAN_ID_CONFIG_RESPONSE and len(payload) >= 3:
            self._apply_config_response(ctx, payload)
            return True
        return False

    def load_control_tab_outputs(self, module_id: int | None = None) -> None:
        mid = int(module_id if module_id is not None else self.current_module_id)
        self.set_current_module(mid)
        ctx = self.context(mid)
        if not ctx.gpio_info:
            self.read_gpio_roles_from_module()
        self.sync_relay_pulse_cache()
        self.read_relay_states_from_module()

    def set_relay_state(self, module_id: int, relay_no: int, state: str) -> dict[str, Any]:
        state_map = {"on": 1, "off": 0, "toggle": 2}
        code = state_map.get(str(state).lower())
        if code is None:
            return {"ok": False, "error": "invalid state"}
        mid = int(module_id)
        rn = int(relay_no)
        reserved = self._used_shutter_relays(mid)
        if rn in reserved:
            return {"ok": False, "error": "relay assigned to shutter", "module_id": mid, "relay_no": rn}
        pulse_ms = self.relay_pulse_ms_for(mid, rn)
        if pulse_ms > 0 and code == 2:
            code = 1
        self._io_acquire()
        try:
            resp = self.send_request(mid, COMMAND_SET_RELAY_STATE, [rn, code], timeout=0.35, log_traffic=False)
            if resp is not None and len(resp) >= 5 and int(resp[2]) == 0:
                is_on = bool(int(resp[4]))
                self._store_relay_state(mid, rn, is_on)
            elif resp is not None and len(resp) >= 3 and int(resp[2]) != 0:
                return {"ok": False, "error": f"status={int(resp[2])}", "module_id": mid, "relay_no": rn}
            else:
                self.collect_relay_state_frames(0.35)
                is_on = bool(self.context(mid).virtual_relay_values.get(rn, 0))
                if resp is None and rn not in self.context(mid).virtual_relay_values:
                    return {"ok": False, "error": "no response", "module_id": mid, "relay_no": rn}
            if pulse_ms > 0 and code == 1 and is_on:
                threading.Timer(max(0.05, (pulse_ms + 80) / 1000.0), lambda: self._pulse_resync(mid, rn)).start()
            elif code in (0, 1):
                self.collect_relay_state_frames(0.35)
            self._io.notify()
            return {"ok": True, "module_id": mid, "relay_no": rn, "state": state, "on": is_on, "pulse_ms": pulse_ms}
        finally:
            self._io_release()

    def collect_relay_state_frames(self, timeout_s: float = 1.0) -> None:
        self._io.sync_transport_macs()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            message = self._safe_recv(min(0.05, max(0.0, deadline - time.time())))
            if message is None:
                continue
            normalized = self._normalize(message)
            if normalized is None:
                continue
            self.handle_can_message(normalized, already_normalized=True)

    def refresh_all_module_relay_states(
        self, *, passive_timeout_s: float = 1.2, active: bool = True
    ) -> None:
        """Passive 0x600/0x602 (+ active GET_GPIO gdy brak telemetrii)."""
        self._io.sync_transport_macs()
        self.collect_relay_state_frames(passive_timeout_s)
        if not active:
            return
        for item in self.discovered_modules:
            mid = int(item.get("module_id", 0))
            if mid in UNKNOWN_MODULE_IDS:
                continue
            ctx = self.context(mid)
            if ctx.virtual_relay_values and any(ctx.virtual_relay_values.values()):
                continue
            self.set_current_module(mid)
            if ctx.relay_gpio_by_index:
                self._read_relay_states_via_gpio_map()
            elif ctx.gpio_info:
                self.read_relay_states_from_module()

    def relay_pulse_ms_for(self, module_id: int, relay_no: int) -> int:
        ctx = self.context(module_id)
        return int(ctx.relay_pulse_ms_by_index.get(int(relay_no), 0))

    def export_module_dict(self, module_id: int) -> dict[str, Any]:
        ctx = self.context(module_id)
        mod_meta = next((m for m in self.discovered_modules if m.get("module_id") == module_id), {})
        relays = []
        for rn, val in sorted(ctx.virtual_relay_values.items()):
            relays.append(
                {
                    "relay_no": rn,
                    "on": bool(val),
                    "pulse_ms": ctx.relay_pulse_ms_by_index.get(rn, 0),
                    "source": "mcp23017" if rn >= MCP23017_RELAY_BASE_INDEX else "local",
                }
            )
        shutter_map = {
            str(k): [v.get("up", 0), v.get("down", 0)] for k, v in ctx.shutter_relay_pairs.items()
        }
        gpio_roles = {
            str(k): {
                "gpio": k,
                "role": v.get("role"),
                "role_name": ROLE_NAME_BY_CODE.get(v.get("role", 0), "Unused"),
                "index": v.get("index", 0),
                "flags": v.get("flags", 0),
            }
            for k, v in ctx.gpio_info.items()
        }
        gpio_values = {
            str(k): {"gpio": k, **v} for k, v in ctx.gpio_values.items()
        }
        relay_gpio_map: dict[int, int] = dict(ctx.relay_gpio_by_index)
        for gpio, info in ctx.gpio_info.items():
            if info.get("role") == PIN_ROLE_MAP["Relay"]:
                idx = int(info.get("index", 0))
                if idx > 0:
                    relay_gpio_map[idx] = gpio
        shutters = [
            {
                "shutter_no": sid,
                "position": st.get("position"),
                "direction": st.get("direction"),
                "direction_text": SHUTTER_DIRECTION_TEXT.get(st.get("direction"), "unknown"),
            }
            for sid, st in sorted(ctx.shutter_states.items())
        ]
        runtime = {
            "relays": relays,
            "shutters": shutters,
            "shutter_map": shutter_map,
            "relay_gpio_map": {str(k): v for k, v in relay_gpio_map.items()},
            "relay_pulse_ms": {str(k): v for k, v in ctx.relay_pulse_ms_by_index.items()},
            "mcp_relay_pins": {str(k): sorted(v) for k, v in ctx.mcp_relay_pins.items()},
            "button_timing": dict(ctx.button_timing),
            "mappings": list(ctx.mappings),
            "hw_flags": ctx.hw_flags,
            "sensors": list(ctx.sensors),
            "gpio_roles": gpio_roles,
            "gpio_values": gpio_values,
        }
        control_relays = self._control_relays(module_id)
        return {
            "module_id": module_id,
            "hw_type": mod_meta.get("hw_type", ctx.hw_type),
            "hw_name": HW_TYPE_NAME_MAP.get(mod_meta.get("hw_type", ctx.hw_type), "unknown"),
            "mac": self._mac_label(mod_meta.get("module_mac")),
            "name": mod_meta.get("name") or ctx.name,
            "firmware_build": ctx.firmware_build,
            "summary_details": ctx.summary_details or mod_meta.get("details", ""),
            "button_count": ctx.button_count,
            "relay_count": ctx.relay_count,
            "shutter_count": ctx.shutter_count,
            "last_seen_s": ctx.last_seen_s,
            "has_master_key": mod_meta.get("has_master_key"),
            "key_mismatch": module_id in self._module_key_mismatch,
            "runtime": runtime,
            "control_relays": control_relays,
        }

    def list_modules(self) -> list[dict[str, Any]]:
        out = []
        seen: set[int] = set()
        for item in self.discovered_modules:
            mid = int(item.get("module_id", 0))
            if mid in seen:
                continue
            seen.add(mid)
            row = self.export_module_dict(mid)
            del row["runtime"]
            del row["control_relays"]
            out.append(row)
        for mid in sorted(self._contexts.keys()):
            if mid not in seen:
                row = self.export_module_dict(mid)
                del row["runtime"]
                del row["control_relays"]
                out.append(row)
        return sorted(out, key=lambda r: r["module_id"])

    # --- tab load (jak _build_tab_load_steps) ---

    def build_tab_load_steps(self, module_id: int, tab_index: int) -> list[tuple[str, Callable[[], None]]]:
        steps: list[tuple[str, Callable[[], None]]] = []
        self.set_current_module(module_id)

        if tab_index == TAB_MODULES:
            steps.append(("Stan MASTER_KEY...", lambda: self._sync_module_master_key_state(module_id)))
            steps.append(("Weryfikacja MASTER_KEY...", lambda: self._probe_module_key_match(module_id)))
            if not self._module_key_mismatch_detected(module_id):
                steps.append(("Podsumowanie modulu...", lambda: self.get_summary(include_gpio_roles=False)))
            return steps

        if tab_index == TAB_GPIO:
            steps.append(("Podsumowanie modulu...", lambda: self._ensure_summary(module_id)))
            steps.append(("Czasy przyciskow...", lambda: self.get_button_timing()))
            steps.append(("Role GPIO...", lambda: self.read_gpio_roles_from_module()))
            return steps

        if tab_index == TAB_CONTROL:
            steps.append(("Podsumowanie modulu...", lambda: self._ensure_summary(module_id)))
            steps.append(("Lista wyjsc...", lambda: self.load_control_tab_outputs(module_id)))
            return steps

        if tab_index == TAB_SHUTTERS:

            def load_shutters() -> None:
                self._ensure_summary(module_id)
                if not self._module_has_master_key(module_id):
                    self.context(module_id).shutter_relay_pairs.clear()
                    return
                shutters_count = int(self.context(module_id).shutter_count or 0)
                if shutters_count > 0:
                    self._shutter_read_all(module_id)
                else:
                    self.context(module_id).shutter_relay_pairs.clear()

            steps.append(("Konfiguracja rolet...", load_shutters))
            return steps

        if tab_index == TAB_MAPPING:
            steps.append(("Mapowania...", lambda: self._load_mappings(module_id)))
            return steps

        if tab_index == TAB_SENSORS:
            steps.append(("Przygotowanie sensorow...", lambda: self.context(module_id).sensors.clear()))
            steps.append(("NTC z konfiguracji...", lambda: self._refresh_ntc_from_roles(module_id)))
            return steps

        return steps

    # --- protocol helpers ---

    def send_request(
        self,
        target_id: int,
        command: int,
        args: list[int] | None = None,
        *,
        timeout: float = 1.0,
        log_traffic: bool = True,
        bypass_config_lock: bool = False,
    ) -> list[int] | None:
        if not self._io.bus_ok():
            return None
        if self._is_module_comm_blocked(target_id, command, bypass_lock=bypass_config_lock):
            self._io.log(f"Blocked cmd 0x{command:02X} ID={target_id} (MASTER_KEY mismatch)")
            return None
        payload = [target_id, command, 0, 0, 0, 0, 0, 0]
        if args:
            for index, value in enumerate(args[:6]):
                payload[2 + index] = int(value) & 0xFF
        acquired = self._io_depth == 0
        if acquired:
            self._io_acquire()
        try:
            if self._send_request_use_secure_tlv(target_id, payload):
                return self._secure_tlv_send_and_wait(target_id, command, payload, timeout, log_traffic)
            self._secure_bus_send(target_id, CAN_ID_CONFIG_REQUEST, payload, log_traffic=log_traffic)
            return self.wait_for_response(target_id, command, timeout=timeout, log_traffic=log_traffic)
        finally:
            if acquired:
                self._io_release()

    def wait_for_response(
        self,
        target_id: int,
        command: int,
        *,
        timeout: float = 1.0,
        log_traffic: bool = True,
    ) -> list[int] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._safe_recv(min(0.02, max(0.0, deadline - time.time())))
            if message is None:
                continue
            message = self._normalize(message)
            if message is None:
                continue
            if message.arbitration_id != CAN_ID_CONFIG_RESPONSE:
                self.handle_can_message(message)
                continue
            payload = list(message.data)
            if log_traffic:
                self._io.log(f"RX 0x711 {payload}")
            if len(payload) < 4 or payload[1] != command:
                continue
            if target_id != 0xFF and payload[0] != target_id and command not in (
                COMMAND_SET_MODULE_ID,
                COMMAND_SET_MODULE_ID_BY_MAC,
            ):
                continue
            return payload
        return None

    def get_summary(self, *, include_gpio_roles: bool = True) -> None:
        mid = self.current_module_id
        response = self.send_request(mid, COMMAND_GET_SUMMARY, log_traffic=False)
        if response is None:
            return
        ctx = self.context(mid)
        ctx.last_summary_response = list(response)
        if len(response) > 7:
            ctx.hw_flags = int(response[7])
        self._apply_summary_counts(mid, response)
        ctx.summary_details = self.build_summary_details(response)
        if not self._module_has_master_key(mid) or not include_gpio_roles:
            return
        self.read_gpio_roles_from_module(summary=response)

    def get_button_timing(self) -> None:
        mid = self.current_module_id
        resp = self.send_request(mid, COMMAND_GET_BUTTON_TIMING, timeout=0.5, log_traffic=False)
        if resp and len(resp) >= 5 and resp[2] == 0:
            self.context(mid).button_timing = {"multiclick_ms": int(resp[3]) * 10, "longpress_ms": int(resp[4]) * 10}

    def read_gpio_roles_from_module(self, *, summary: list[int] | None = None) -> None:
        mid = self.current_module_id
        ctx = self.context(mid)
        summary = summary if summary is not None else ctx.last_summary_response
        shutters_count, hc595_regs, mcp_present, _mcp_offset = self._summary_hw_flags(summary)
        self.get_all_gpio_roles()
        if shutters_count > 0:
            for shutter_num in range(1, MAX_SHUTTERS + 1):
                resp = self.send_request(
                    mid, COMMAND_GET_SHUTTER_RELAYS, [shutter_num], timeout=0.15, log_traffic=False
                )
                if resp and len(resp) >= 6 and resp[4] != 0 and resp[5] != 0:
                    ctx.shutter_relay_pairs[shutter_num] = {"up": int(resp[4]), "down": int(resp[5])}
                else:
                    ctx.shutter_relay_pairs.pop(shutter_num, None)
        else:
            ctx.shutter_relay_pairs.clear()
        if hc595_regs > 0:
            total_q = hc595_regs * SHIFT595_RELAY_COUNT_PER_REGISTER
            for q_index in range(total_q):
                resp = self.send_request(
                    mid, COMMAND_GET_SHIFT595_FLAGS, [q_index], timeout=0.15, log_traffic=False
                )
                if resp and len(resp) >= 5 and resp[2] == 0:
                    relay_idx = SHIFT595_RELAY_BASE_INDEX + q_index
                    ctx.shift595_q_flags[relay_idx] = int(resp[4])
        if mcp_present:
            found_mask = self.scan_mcp23017(timeout=0.45)
            ctx.mcp23017_found_mask = found_mask
            for chip in range(8):
                if not (found_mask & (1 << chip)):
                    continue
                resp = self.send_request(
                    mid, COMMAND_GET_MCP23017_ROLE_DUMP, [chip], timeout=0.25, log_traffic=False
                )
                if resp and len(resp) >= 7 and resp[2] == 0:
                    ctx.mcp_relay_pins[chip] = self._parse_mcp_role_dump(resp[4:8])
        self.sync_relay_pulse_cache()

    def get_all_gpio_roles(self) -> None:
        mid = self.current_module_id
        ctx = self.context(mid)
        profile = self._pinout_profile(ctx.hw_type)
        if profile is None or mid in UNKNOWN_MODULE_IDS or not self._module_has_master_key(mid):
            return
        gpios = self._profile_gpios(profile)
        occupied: set[int] = set()
        for gpio in gpios:
            resp = self.send_request(mid, COMMAND_GET_GPIO_ROLE, [gpio], timeout=0.1, log_traffic=False)
            if resp and len(resp) >= 7 and resp[2] == 0:
                role_code = int(resp[3])
                ctx.gpio_info[gpio] = {"role": role_code, "index": int(resp[4]), "flags": int(resp[5])}
                if role_code != PIN_ROLE_MAP["Unused"]:
                    occupied.add(gpio)
            else:
                ctx.gpio_info[gpio] = {"role": PIN_ROLE_MAP["Unused"], "index": 0, "flags": 0}

    def read_relay_states_from_module(self) -> None:
        mid = self.current_module_id
        if mid in UNKNOWN_MODULE_IDS:
            return
        ctx = self.context(mid)
        if not ctx.gpio_info and ctx.relay_gpio_by_index:
            self._read_relay_states_via_gpio_map()
            return
        for gpio, info in ctx.gpio_info.items():
            if info.get("role") != PIN_ROLE_MAP["Relay"]:
                continue
            relay_index = int(info.get("index", 0))
            if relay_index <= 0:
                continue
            resp = self.send_request(mid, COMMAND_GET_GPIO_VALUE, [gpio], timeout=0.15, log_traffic=False)
            if resp is None or len(resp) < 7 or resp[2] != 0 or resp[6] != 1:
                continue
            self._store_relay_state(mid, relay_index, int(resp[3]))
        self.collect_relay_state_frames(0.75)

    def sync_relay_pulse_cache(self, *, force: bool = False) -> None:
        mid = self.current_module_id
        ctx = self.context(mid)
        for relay_num in self._iter_configured_relay_numbers(mid):
            if not force and relay_num in ctx.relay_pulse_ms_by_index:
                continue
            resp = self.send_request(mid, COMMAND_GET_RELAY_PULSE, [relay_num], timeout=0.15, log_traffic=False)
            if resp and len(resp) >= 6 and resp[2] == 0:
                pulse = int(resp[4]) | (int(resp[5]) << 8)
                ctx.relay_pulse_ms_by_index[relay_num] = pulse

    def scan_mcp23017(self, *, timeout: float = 0.7) -> int:
        mid = self.current_module_id
        resp = self.send_request(mid, COMMAND_SCAN_MCP23017, timeout=timeout, log_traffic=False)
        if resp is None or len(resp) < 4 or resp[2] != 0:
            return 0
        return int(resp[3]) if len(resp) > 3 else 0

    @staticmethod
    def build_summary_details(response: list[int]) -> str:
        shutters = response[6] if len(response) > 6 else 0
        hw_flags = response[7] if len(response) > 7 else 0
        hc595_regs = (hw_flags >> 4) & 0x07
        mcp_present = bool(hw_flags & 0x08)
        mcp_offset = hw_flags & 0x07
        mcp_info = f", mcp=0x{0x20 + mcp_offset:02X}" if mcp_present else ""
        hc595_info = f", hc595_regs={hc595_regs}" if hc595_regs else ""
        return (
            f"buttons={response[3]}, relays={response[4]}, ds18b20={response[5]}, "
            f"shutters={shutters}{hc595_info}{mcp_info}"
        )

    # --- internal ---

    def _load_mappings(self, module_id: int) -> None:
        if self._read_mappings is None:
            raise RuntimeError("read_mappings callback not configured")
        result = self._read_mappings(self, module_id)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error", "read mappings failed")))
        self.context(module_id).mappings = list(result.get("rows") or result.get("mappings") or [])

    def _refresh_ntc_from_roles(self, module_id: int) -> None:
        ctx = self.context(module_id)
        if not ctx.gpio_info:
            self.read_gpio_roles_from_module()
        has_ntc = any(int(v.get("role", 0)) == PIN_ROLE_MAP["NTC"] for v in ctx.gpio_info.values())
        if has_ntc:
            self.collect_relay_state_frames(0.8)

    def _shutter_read_all(self, module_id: int) -> None:
        ctx = self.context(module_id)
        for shutter_no in range(1, MAX_SHUTTERS + 1):
            resp = self.send_request(
                module_id, COMMAND_GET_SHUTTER_RELAYS, [shutter_no], timeout=0.25, log_traffic=False
            )
            if resp and len(resp) >= 6 and resp[4] != 0 and resp[5] != 0:
                ctx.shutter_relay_pairs[shutter_no] = {"up": int(resp[4]), "down": int(resp[5])}
            else:
                ctx.shutter_relay_pairs.pop(shutter_no, None)
        self.collect_relay_state_frames(0.35)

    def _ensure_summary(self, module_id: int) -> None:
        ctx = self.context(module_id)
        if ctx.last_summary_response and ctx.last_summary_response[0] == module_id:
            return
        self.set_current_module(module_id)
        self.get_summary(include_gpio_roles=False)

    def _pulse_resync(self, module_id: int, relay_no: int) -> None:
        self.set_current_module(module_id)
        self._io_acquire()
        try:
            self.collect_relay_state_frames(0.45)
            self._store_relay_state(module_id, relay_no, False)
            self._io.notify()
        finally:
            self._io_release()

    def _control_relays(self, module_id: int) -> list[dict[str, Any]]:
        ctx = self.context(module_id)
        reserved = self._used_shutter_relays(module_id)
        nums = set(self._iter_configured_relay_numbers(module_id))
        nums.difference_update(reserved)
        if not nums:
            nums = set(range(1, 17))
        out = []
        for rn in sorted(nums):
            on = bool(ctx.virtual_relay_values.get(rn, 0))
            out.append(
                {
                    "relay_no": rn,
                    "on": on,
                    "pulse_ms": ctx.relay_pulse_ms_by_index.get(rn, 0),
                    "source": "mcp23017" if rn >= MCP23017_RELAY_BASE_INDEX else "local",
                    "shutter_reserved": rn in reserved,
                }
            )
        return out

    def _iter_configured_relay_numbers(self, module_id: int) -> set[int]:
        ctx = self.context(module_id)
        nums: set[int] = set()
        for info in ctx.gpio_info.values():
            if info.get("role") == PIN_ROLE_MAP["Relay"]:
                idx = int(info.get("index", 0))
                if idx > 0:
                    nums.add(idx)
        regs = (ctx.hw_flags >> 4) & 0x07
        if regs > 0:
            nums.update(
                range(
                    SHIFT595_RELAY_BASE_INDEX,
                    SHIFT595_RELAY_BASE_INDEX + regs * SHIFT595_RELAY_COUNT_PER_REGISTER,
                )
            )
        for chip, pins in ctx.mcp_relay_pins.items():
            for lp in pins:
                nums.add(MCP23017_RELAY_BASE_INDEX + int(chip) * 16 + int(lp))
        nums.update(ctx.virtual_relay_values.keys())
        return nums

    def _used_shutter_relays(self, module_id: int) -> set[int]:
        reserved: set[int] = set()
        for pair in self.context(module_id).shutter_relay_pairs.values():
            if pair.get("up", 0) > 0:
                reserved.add(int(pair["up"]))
            if pair.get("down", 0) > 0:
                reserved.add(int(pair["down"]))
        return reserved

    def _store_relay_state(self, module_id: int, relay_index: int, state: int) -> None:
        ctx = self.context(module_id)
        ctx.virtual_relay_values[int(relay_index)] = int(state)
        for gpio, info in ctx.gpio_info.items():
            if info.get("role") != PIN_ROLE_MAP["Relay"]:
                continue
            if int(info.get("index", 0)) != relay_index:
                continue
            flags = int(info.get("flags", 0))
            active_high = (flags & 0x01) == 0
            raw = state if active_high else (0 if state else 1)
            ctx.gpio_values[gpio] = {
                "logical": state,
                "raw": raw,
                "role": PIN_ROLE_MAP["Relay"],
                "index": relay_index,
            }
            break

    def _read_relay_states_via_gpio_map(self) -> None:
        mid = self.current_module_id
        if mid in UNKNOWN_MODULE_IDS:
            return
        ctx = self.context(mid)
        for relay_index, gpio in sorted(ctx.relay_gpio_by_index.items()):
            if int(relay_index) <= 0 or int(gpio) <= 0:
                continue
            resp = self.send_request(
                mid, COMMAND_GET_GPIO_VALUE, [int(gpio)], timeout=0.15, log_traffic=False
            )
            if resp is None or len(resp) < 7 or resp[2] != 0 or resp[6] != 1:
                continue
            self._store_relay_state(mid, int(relay_index), int(resp[3]))
        self.collect_relay_state_frames(0.35)

    def _apply_relay_gpio_map_frame(self, ctx: ModuleContext, payload: list[int]) -> bool:
        if len(payload) < 3:
            return False
        start = int(payload[1])
        changed = False
        for offset, gpio_raw in enumerate(payload[2:8]):
            relay_index = start + offset
            gpio = int(gpio_raw)
            if gpio == 0xFF:
                if relay_index in ctx.relay_gpio_by_index:
                    del ctx.relay_gpio_by_index[relay_index]
                    changed = True
                continue
            if ctx.relay_gpio_by_index.get(relay_index) != gpio:
                changed = True
            ctx.relay_gpio_by_index[relay_index] = gpio
        if changed:
            self._io.notify()
        return changed

    def _apply_relays_frame(self, ctx: ModuleContext, payload: list[int]) -> bool:
        if len(payload) < 3:
            return False
        lo = int(payload[1])
        hi = int(payload[2])
        ext_bytes = payload[3:]
        ext_bits = 0
        for byte_index, byte_value in enumerate(ext_bytes):
            ext_bits |= (int(byte_value) & 0xFF) << (8 * byte_index)
        changed = False

        def _set_relay(relay_index: int, relay_on: int) -> None:
            nonlocal changed
            relay_index = int(relay_index)
            relay_on = int(relay_on)
            if ctx.virtual_relay_values.get(relay_index) != relay_on:
                changed = True
            ctx.virtual_relay_values[relay_index] = relay_on

        for relay_index in range(1, 17):
            if relay_index <= 8:
                relay_on = 1 if (lo & (1 << (relay_index - 1))) else 0
            else:
                relay_on = 1 if (hi & (1 << (relay_index - 9))) else 0
            _set_relay(relay_index, relay_on)

        regs = (ctx.hw_flags >> 4) & 0x07
        if regs <= 0 and ext_bytes:
            regs = len(ext_bytes)
        if regs > 0:
            total = regs * SHIFT595_RELAY_COUNT_PER_REGISTER
            for relay_index in range(SHIFT595_RELAY_BASE_INDEX, SHIFT595_RELAY_BASE_INDEX + total):
                shift_offset = relay_index - SHIFT595_RELAY_BASE_INDEX
                relay_on = 1 if (ext_bits & (1 << shift_offset)) else 0
                _set_relay(relay_index, relay_on)

        if ctx.gpio_info:
            for gpio, info in ctx.gpio_info.items():
                if info.get("role") != PIN_ROLE_MAP["Relay"]:
                    continue
                relay_index = int(info.get("index", 0))
                if relay_index <= 0:
                    continue
                relay_on = int(ctx.virtual_relay_values.get(relay_index, 0))
                flags = int(info.get("flags", 0))
                active_high = (flags & 0x01) == 0
                raw = relay_on if active_high else (0 if relay_on else 1)
                new_val = {
                    "logical": relay_on,
                    "raw": raw,
                    "role": PIN_ROLE_MAP["Relay"],
                    "index": relay_index,
                }
                if ctx.gpio_values.get(gpio) != new_val:
                    changed = True
                ctx.gpio_values[gpio] = new_val

        if changed:
            self._io.notify()
        return changed

    def _apply_mcp_relays_frame(self, ctx: ModuleContext, payload: list[int]) -> bool:
        if len(payload) < 4:
            return False
        chip_offset = int(payload[1])
        gpa = int(payload[2])
        gpb = int(payload[3])
        for local_pin in range(16):
            if local_pin < 8:
                on = 1 if (gpa & (1 << local_pin)) else 0
            else:
                on = 1 if (gpb & (1 << (local_pin - 8))) else 0
            relay_index = MCP23017_RELAY_BASE_INDEX + chip_offset * MCP23017_OUTPUT_COUNT + local_pin
            ctx.virtual_relay_values[relay_index] = on
        self._io.notify()
        return True

    def _apply_config_response(self, ctx: ModuleContext, data: list[int]) -> None:
        cmd = int(data[1])
        status = int(data[2])
        if status != 0:
            return
        if cmd == COMMAND_GET_SUMMARY:
            ctx.summary_details = self.build_summary_details(data)
            self._apply_summary_counts(ctx.module_id, data)
        elif cmd == COMMAND_GET_MODULE_NAME:
            ctx.name = self._ascii_from_bytes(data[3:]) or None
        elif cmd == COMMAND_GET_BUILD_INFO and len(data) >= 8:
            ctx.firmware_build = f"{2000 + data[3]:04d}.{data[4]:02d}.{data[5]:02d} {data[6]:02d}:{data[7]:02d}"
        elif cmd == COMMAND_GET_SHUTTER_RELAYS and len(data) >= 6:
            sid = int(data[3])
            if data[4] != 0 and data[5] != 0:
                ctx.shutter_relay_pairs[sid] = {"up": int(data[4]), "down": int(data[5])}
            else:
                ctx.shutter_relay_pairs.pop(sid, None)
        elif cmd == COMMAND_GET_MCP23017_ROLE_DUMP and len(data) >= 7:
            chip = int(data[3])
            ctx.mcp_relay_pins[chip] = self._parse_mcp_role_dump(data[4:8])
        elif cmd == COMMAND_GET_RELAY_PULSE and len(data) >= 6:
            rn = int(data[3])
            ctx.relay_pulse_ms_by_index[rn] = int(data[4]) | (int(data[5]) << 8)
        elif cmd == COMMAND_SET_RELAY_STATE and len(data) >= 5:
            self._store_relay_state(ctx.module_id, int(data[3]), int(data[4]))
        elif cmd == COMMAND_GET_BUTTON_TIMING and len(data) >= 5:
            ctx.button_timing = {"multiclick_ms": int(data[3]) * 10, "longpress_ms": int(data[4]) * 10}
        self._io.notify()

    def _apply_summary_counts(self, module_id: int, raw: list[int]) -> None:
        ctx = self.context(module_id)
        if len(raw) >= 8:
            ctx.button_count = int(raw[3])
            ctx.relay_count = int(raw[4])
            ctx.shutter_count = int(raw[6])
            ctx.hw_flags = int(raw[7])

    def _summary_hw_flags(self, summary: list[int] | None) -> tuple[int, int, bool, int]:
        if not summary or len(summary) < 8:
            return 0, 0, False, 0
        hw_flags = int(summary[7])
        shutters = int(summary[6])
        hc595_regs = (hw_flags >> 4) & 0x07
        mcp_present = bool(hw_flags & 0x08)
        mcp_offset = hw_flags & 0x07
        return shutters, hc595_regs, mcp_present, mcp_offset

    def _sync_module_master_key_state(self, module_id: int) -> None:
        if module_id in UNKNOWN_MODULE_IDS:
            return
        state = self.send_request(
            module_id, COMMAND_PROVISION_GET_MASTER_KEY_STATE, timeout=0.35, log_traffic=False
        )
        if state is not None and len(state) >= 4 and state[2] == 0:
            has_key = bool(state[3])
            for item in self.discovered_modules:
                if item.get("module_id") == module_id:
                    item["has_master_key"] = has_key
                    break

    def _probe_module_key_match(self, module_id: int) -> None:
        if module_id in UNKNOWN_MODULE_IDS or not self._module_has_master_key(module_id):
            self._module_key_mismatch.discard(module_id)
            return
        if self._secure_tlv_node_key(module_id) is None:
            return
        response = self.send_request(
            module_id,
            COMMAND_GET_SUMMARY,
            timeout=1.0,
            log_traffic=False,
            bypass_config_lock=True,
        )
        if response is not None and len(response) >= 3 and response[2] == 0:
            self._module_key_mismatch.discard(module_id)
            return
        state = self.send_request(
            module_id,
            COMMAND_PROVISION_GET_MASTER_KEY_STATE,
            timeout=0.8,
            log_traffic=False,
        )
        if state is not None and len(state) >= 4 and state[2] == 0 and state[3] == 1:
            self._module_key_mismatch.add(module_id)

    def _module_has_master_key(self, module_id: int) -> bool:
        if module_id in UNKNOWN_MODULE_IDS:
            return False
        for item in self.discovered_modules:
            if item.get("module_id") == module_id:
                state = item.get("has_master_key")
                if state is True:
                    return True
                if state is False:
                    return False
                break
        return False

    def _module_key_mismatch_detected(self, module_id: int) -> bool:
        return module_id in self._module_key_mismatch

    def _is_module_comm_blocked(self, module_id: int, command: int, *, bypass_lock: bool) -> bool:
        if bypass_lock or module_id in UNKNOWN_MODULE_IDS:
            return False
        if module_id not in self._module_key_mismatch:
            return False
        if command in (COMMAND_SET_MODULE_ID, COMMAND_SET_MODULE_ID_BY_MAC, COMMAND_PROVISION_GET_MASTER_KEY_STATE):
            return False
        return True

    def _read_module_name(self, module_id: int) -> str | None:
        resp = self.send_request(module_id, COMMAND_GET_MODULE_NAME, timeout=0.15, log_traffic=False)
        if resp is None or len(resp) < 3 or resp[2] != 0:
            return None
        return self._ascii_from_bytes(resp[3:]) or None

    def _read_module_build(self, module_id: int) -> str | None:
        resp = self.send_request(module_id, COMMAND_GET_BUILD_INFO, timeout=0.15, log_traffic=False)
        if resp is None or len(resp) < 8 or resp[2] != 0:
            return None
        return f"{2000 + resp[3]:04d}.{resp[4]:02d}.{resp[5]:02d} {resp[6]:02d}:{resp[7]:02d}"

    def _upsert_discovered(
        self,
        module_id: int,
        *,
        hw_type: int | None = None,
        module_mac: int | None = None,
        details: str | None = None,
    ) -> bool:
        now = time.time()
        existing = None
        for item in self.discovered_modules:
            if module_id not in UNKNOWN_MODULE_IDS and item.get("module_id") == module_id:
                existing = item
                break
            if module_mac is not None and item.get("module_mac") == module_mac:
                existing = item
                break
        created = False
        if existing is None:
            existing = {
                "module_id": module_id,
                "details": details or "brak summary",
                "hw_type": hw_type if hw_type is not None else HW_TYPE_OTHER,
                "module_mac": module_mac,
                "has_master_key": None,
                "last_seen": now,
            }
            self.discovered_modules.append(existing)
            created = True
        changed = created
        if module_id not in UNKNOWN_MODULE_IDS and existing.get("module_id") != module_id:
            existing["module_id"] = module_id
            changed = True
        if hw_type is not None and existing.get("hw_type") != hw_type:
            existing["hw_type"] = hw_type
            changed = True
        if module_mac is not None and existing.get("module_mac") != module_mac:
            existing["module_mac"] = module_mac
            changed = True
        if details is not None and existing.get("details") != details:
            existing["details"] = details
            changed = True
        existing["last_seen"] = now
        ctx = self.context(module_id if module_id not in UNKNOWN_MODULE_IDS else existing.get("module_id", module_id))
        ctx.last_seen_s = now
        if hw_type is not None:
            ctx.hw_type = hw_type
        if details is not None:
            ctx.summary_details = details
        if module_mac is not None:
            self._io.invalidate_transport_macs()
        return changed

    def _secure_bus_send(
        self, target_module_id: int, can_id: int, data: list[int], *, log_traffic: bool = True
    ) -> None:
        raw = bytes(int(b) & 0xFF for b in data)
        module_has_key = (
            target_module_id not in UNKNOWN_MODULE_IDS and self._module_has_master_key(target_module_id)
        )
        if (
            target_module_id not in UNKNOWN_MODULE_IDS
            and target_module_id in self._module_key_mismatch
            and not is_plaintext_bootstrap_tx(can_id, raw, module_has_master_key=module_has_key)
        ):
            raise RuntimeError(f"Module ID={target_module_id} blocked (MASTER_KEY mismatch)")
        if is_plaintext_bootstrap_tx(can_id, raw, module_has_master_key=module_has_key) or can_id in PLAINTEXT_TELEMETRY_CAN_IDS:
            self._io.send_can_frame(can_id, list(raw))
            return
        if self._master_key is None:
            raise RuntimeError("MASTER_KEY required for secure CAN")
        frames = self._io.prepare_outgoing_frames(target_module_id, can_id, data)
        if frames is None:
            raise RuntimeError(f"No MAC/key for module {target_module_id}")
        for frame_id, payload in frames:
            self._io.send_can_frame(int(frame_id), list(payload))

    def _refresh_secure_transport(self) -> None:
        self._io.sync_transport_macs()

    def _send_request_use_secure_tlv(self, target_id: int, payload: list[int]) -> bool:
        if self._master_key is None or target_id in UNKNOWN_MODULE_IDS:
            return False
        if not self._module_has_master_key(target_id):
            return False
        return self._secure_tlv_node_key(target_id) is not None

    def _secure_tlv_node_key(self, module_id: int) -> bytes | None:
        module_mac = None
        for item in self.discovered_modules:
            if item.get("module_id") == module_id:
                module_mac = item.get("module_mac")
                break
        if module_mac is None or self._master_key is None:
            return None
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
        digest = hmac.new(self._master_key, b"CAN-NODE-KEY|v1|" + mac_b, sha256).digest()
        return digest[:16]

    def _secure_tlv_send_and_wait(
        self, target_id: int, command: int, payload: list[int], timeout: float, log_traffic: bool
    ) -> list[int] | None:
        self._secure_bus_send(target_id, CAN_ID_CONFIG_REQUEST, payload, log_traffic=log_traffic)
        return self.wait_for_response(target_id, command, timeout=timeout, log_traffic=log_traffic)

    def _normalize(self, message: Any) -> Any | None:
        return self._io.normalize(message)

    def _safe_recv(self, timeout: float) -> Any | None:
        return self._io.recv(timeout)

    def _drain_rx(self) -> None:
        while True:
            msg = self._safe_recv(0.02)
            if msg is None:
                break

    def _io_acquire(self) -> None:
        self._io.io_acquire()
        self._io_depth += 1

    def _io_release(self) -> None:
        self._io_depth = max(0, self._io_depth - 1)
        self._io.io_release()

    @staticmethod
    def _extract_device_mac(payload: list[int]) -> int | None:
        if len(payload) < 8:
            return None
        mac = 0
        for i in range(2, 8):
            mac = (mac << 8) | (payload[i] & 0xFF)
        return mac

    @staticmethod
    def _mac_label(module_mac: int | None) -> str | None:
        if module_mac is None:
            return None
        return ":".join(f"{(int(module_mac) >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))

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
    def _parse_mcp_role_dump(packed: list[int]) -> set[int]:
        pins: set[int] = set()
        if len(packed) < 4:
            return pins
        for i in range(16):
            b = int(packed[i // 4]) & 0xFF
            role = (b >> ((i % 4) * 2)) & 0x03
            if role == 1:
                pins.add(i)
        return pins

    @staticmethod
    def _pinout_profile(hw_type: int) -> dict[str, Any] | None:
        name = HW_TYPE_TO_PINOUT.get(int(hw_type))
        if name is None:
            return None
        return DEVICE_PINOUTS.get(name)

    @staticmethod
    def _profile_gpios(profile: dict[str, Any]) -> list[int]:
        gpios: list[int] = []
        for key in ("left", "left_inner", "right_inner", "right"):
            for pin_entry in profile.get(key, []):
                if isinstance(pin_entry, dict):
                    gpio = pin_entry.get("gpio")
                    if gpio is not None and not pin_entry.get("reserved", False):
                        gpios.append(int(gpio))
                elif isinstance(pin_entry, int):
                    gpios.append(int(pin_entry))
        return sorted(set(gpios))
