"""Lazy-load zakładek panelu web — analogicznie do konfiguratora Windows (_schedule_tab_load)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from protocol_constants import (
    COMMAND_GET_BUILD_INFO,
    COMMAND_GET_BUTTON_TIMING,
    COMMAND_GET_MODULE_NAME,
    COMMAND_GET_RELAY_PULSE,
    COMMAND_GET_SHUTTER_RELAYS,
    COMMAND_GET_SUMMARY,
    COMMAND_SCAN_SENSORS,
    MAX_SHUTTERS,
)

from .gpio_service import read_gpio_roles, read_gpio_values, read_relay_pulse_ms
from .mapping_service import read_all_mappings

if TYPE_CHECKING:
    from .bus_manager import BusManager

_VALID_TABS = frozenset({"modules", "gpio", "control", "shutters", "mapping", "sensors"})


def _step(name: str, fn: Callable[[], None]) -> dict[str, Any]:
    try:
        fn()
        return {"name": name, "ok": True}
    except Exception as err:  # noqa: BLE001
        return {"name": name, "ok": False, "error": str(err)}


def _ensure_summary(bus: BusManager, module_id: int) -> None:
    detail = bus.module_detail(module_id)
    if detail and detail.get("summary_details"):
        return
    bus.send_config_and_wait(module_id, COMMAND_GET_SUMMARY, timeout=0.6)
    time.sleep(0.12)


def _load_modules_tab(bus: BusManager, module_id: int) -> dict[str, Any]:
    steps = [
        _step("Podsumowanie modulu", lambda: bus.send_config_and_wait(module_id, COMMAND_GET_SUMMARY, timeout=0.6)),
        _step("Nazwa modulu", lambda: bus.send_config_and_wait(module_id, COMMAND_GET_MODULE_NAME, timeout=0.5)),
        _step("Build info", lambda: bus.send_config_and_wait(module_id, COMMAND_GET_BUILD_INFO, timeout=0.5)),
    ]
    bus.pump_rx(0.3)
    detail = bus.module_detail(module_id)
    return {"ok": True, "tab": "modules", "steps": steps, "module": detail}


def _load_gpio_tab(bus: BusManager, module_id: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    steps.append(_step("Podsumowanie modulu", lambda: _ensure_summary(bus, module_id)))

    def _roles() -> None:
        result = read_gpio_roles(bus, module_id)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error", "read_gpio_roles failed")))

    steps.append(_step("Role GPIO", _roles))

    def _timing() -> None:
        resp = bus.send_config_and_wait(module_id, COMMAND_GET_BUTTON_TIMING, timeout=0.5)
        if resp is None or len(resp) < 5 or int(resp[2]) != 0:
            raise RuntimeError("GET_BUTTON_TIMING brak odpowiedzi")
        bus.store_button_timing(module_id, int(resp[3]) * 10, int(resp[4]) * 10)

    steps.append(_step("Czasy przyciskow", _timing))
    bus.pump_rx(0.2)
    return {
        "ok": all(s.get("ok", False) for s in steps),
        "tab": "gpio",
        "steps": steps,
        "button_timing": (bus.module_detail(module_id) or {}).get("runtime", {}).get("button_timing"),
    }


def _sync_relay_pulses(bus: BusManager, module_id: int, *, only_missing: bool = True) -> None:
    detail = bus.module_detail(module_id) or {}
    known = {int(k) for k in ((detail.get("runtime") or {}).get("relay_pulse_ms") or {})}
    for relay_no in sorted(bus.relay_numbers_for_module(module_id)):
        if only_missing and int(relay_no) in known:
            continue
        read_relay_pulse_ms(bus, module_id, int(relay_no))
        time.sleep(0.02)


def _load_control_tab(bus: BusManager, module_id: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    steps.append(_step("Podsumowanie modulu", lambda: _ensure_summary(bus, module_id)))

    detail = bus.module_detail(module_id) or {}
    rt = detail.get("runtime") or {}
    roles = rt.get("gpio_roles") or {}
    if not roles:
        def _roles() -> None:
            result = read_gpio_roles(bus, module_id)
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error", "read_gpio_roles failed")))

        steps.append(_step("Role GPIO", _roles))

    pulse_map = rt.get("relay_pulse_ms") or {}
    relay_nums = bus.relay_numbers_for_module(module_id)
    known_pulse = {int(k) for k in pulse_map}
    missing_pulse = [rn for rn in relay_nums if int(rn) not in known_pulse]
    if missing_pulse:
        steps.append(
            _step(
                "Impulsy przekaznikow",
                lambda: _sync_relay_pulses(bus, module_id, only_missing=True),
            )
        )

    def _values() -> None:
        read_gpio_values(bus, module_id)

    steps.append(_step("Stany wyjsc", _values))
    deadline = time.time() + 0.45
    while time.time() < deadline:
        bus.pump_rx(0.05)
    return {"ok": True, "tab": "control", "steps": steps}


def _load_shutters_tab(bus: BusManager, module_id: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    steps.append(_step("Podsumowanie modulu", lambda: _ensure_summary(bus, module_id)))
    detail = bus.module_detail(module_id) or {}
    shutter_count = int(detail.get("shutter_count") or 0)

    def _read_shutters() -> None:
        if shutter_count <= 0:
            return
        for shutter_no in range(1, MAX_SHUTTERS + 1):
            bus.send_config_and_wait(module_id, COMMAND_GET_SHUTTER_RELAYS, [shutter_no], timeout=0.2)
            time.sleep(0.06)
        bus.pump_rx(0.4)

    steps.append(_step("Konfiguracja rolet", _read_shutters))
    return {"ok": True, "tab": "shutters", "steps": steps, "shutter_count": shutter_count}


def _load_mapping_tab(bus: BusManager, module_id: int) -> dict[str, Any]:
    result = read_all_mappings(bus, module_id)
    steps = [{"name": "Mapowania", "ok": bool(result.get("ok"))}]
    if not result.get("ok"):
        steps[0]["error"] = result.get("error")
    return {"ok": bool(result.get("ok")), "tab": "mapping", "steps": steps, **result}


def _load_sensors_tab(bus: BusManager, module_id: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    bus.clear_sensors(module_id)

    def _scan() -> None:
        bus.send_config_and_wait(module_id, COMMAND_SCAN_SENSORS, timeout=2.0)
        deadline = time.time() + 2.5
        while time.time() < deadline:
            bus.pump_rx(0.05)

    steps.append(_step("Skan sensorow", _scan))
    detail = bus.module_detail(module_id) or {}
    sensors = list((detail.get("runtime") or {}).get("sensors") or [])
    return {"ok": True, "tab": "sensors", "steps": steps, "sensors": sensors}


def load_module_tab(bus: BusManager, module_id: int, tab: str) -> dict[str, Any]:
    if not bus.ensure_bus():
        return {"ok": False, "error": bus._bus_error or "bus not open"}  # noqa: SLF001

    tab_key = str(tab).strip().lower()
    if tab_key not in _VALID_TABS:
        return {"ok": False, "error": f"unknown tab: {tab}"}

    mid = int(module_id)
    if not (1 <= mid <= 254):
        return {"ok": False, "error": "invalid module_id"}

    if not bus._begin_command_io():  # noqa: SLF001
        return {"ok": False, "error": "bus busy (scan/refresh in progress)"}
    try:
        if tab_key == "modules":
            return _load_modules_tab(bus, mid)
        if tab_key == "gpio":
            return _load_gpio_tab(bus, mid)
        if tab_key == "control":
            return _load_control_tab(bus, mid)
        if tab_key == "shutters":
            return _load_shutters_tab(bus, mid)
        if tab_key == "mapping":
            return _load_mapping_tab(bus, mid)
        if tab_key == "sensors":
            return _load_sensors_tab(bus, mid)
        return {"ok": False, "error": "unsupported tab"}
    finally:
        bus._end_command_io()  # noqa: SLF001
