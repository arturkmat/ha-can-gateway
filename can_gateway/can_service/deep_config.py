"""Głęboki odczyt konfiguracji modułu — jak zakładki konfiguratora."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from protocol_constants import (
    COMMAND_GET_BUILD_INFO,
    COMMAND_GET_MCP23017_ROLE_DUMP,
    COMMAND_GET_SHUTTER_RELAYS,
    COMMAND_GET_SUMMARY,
    COMMAND_SCAN_MCP23017,
    COMMAND_SCAN_SENSORS,
    MAX_SHUTTERS,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager

_MCP_GAP_S = 0.055
_SHUTTER_GAP_S = 0.08
_PULSE_GAP_S = 0.06
_CMD_GAP_S = 0.12


def refresh_module_deep(bus: BusManager, module_id: int) -> dict[str, Any]:
    mid = int(module_id)
    if not bus.bus_ok:
        return {"ok": False, "error": "bus not open"}

    engine = bus._get_engine()  # noqa: SLF001
    engine.set_current_module(mid)

    bus.send_config(mid, COMMAND_GET_SUMMARY)
    time.sleep(0.15)

    name = engine._read_module_name(mid)  # noqa: SLF001
    if name is not None:
        engine.context(mid).name = name
        for item in engine.discovered_modules:
            if item.get("module_id") == mid:
                item["name"] = name
                break

    for cmd in (COMMAND_GET_BUILD_INFO, COMMAND_SCAN_SENSORS, COMMAND_SCAN_MCP23017):
        bus.send_config(mid, cmd)
        time.sleep(_CMD_GAP_S)

    engine.read_gpio_roles_from_module()

    for chip in range(8):
        bus.send_config(mid, COMMAND_GET_MCP23017_ROLE_DUMP, [chip])
        time.sleep(_MCP_GAP_S)

    for shutter_no in range(1, MAX_SHUTTERS + 1):
        bus.send_config(mid, COMMAND_GET_SHUTTER_RELAYS, [shutter_no])
        time.sleep(_SHUTTER_GAP_S)

    relay_nos = bus.relay_numbers_for_module(mid)
    for relay_no in sorted(relay_nos):
        bus.send_config(mid, COMMAND_GET_RELAY_PULSE, [relay_no])
        time.sleep(_PULSE_GAP_S)

    deadline = time.time() + 3.0
    while time.time() < deadline:
        bus.pump_rx(timeout=0.05)

    detail = bus.module_detail(mid)
    return {"ok": True, "module": detail}
