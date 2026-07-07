"""Głęboki odczyt konfiguracji modułu — jak zakładki konfiguratora."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from protocol_constants import (
    COMMAND_GET_BUILD_INFO,
    COMMAND_GET_SUMMARY,
    COMMAND_SCAN_MCP23017,
    COMMAND_SCAN_SENSORS,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager

_CMD_GAP_S = 0.12


def refresh_module_deep(bus: BusManager, module_id: int) -> dict[str, Any]:
    mid = int(module_id)
    if not bus.bus_ok:
        return {"ok": False, "error": "bus not open"}

    engine = bus._get_engine()  # noqa: SLF001
    engine.set_current_module(mid)
    ctx = engine.context(mid)

    summary = bus.send_config_and_wait(mid, COMMAND_GET_SUMMARY, timeout=1.0)
    if summary and len(summary) >= 8 and int(summary[2]) == 0:
        ctx.last_summary_response = list(summary)
        ctx.summary_details = engine.build_summary_details(summary)
        engine._apply_summary_counts(mid, summary)  # noqa: SLF001
    else:
        summary = ctx.last_summary_response

    name = engine._read_module_name(mid)  # noqa: SLF001
    if name is not None:
        ctx.name = name
        for item in engine.discovered_modules:
            if item.get("module_id") == mid:
                item["name"] = name
                break

    for cmd in (COMMAND_GET_BUILD_INFO, COMMAND_SCAN_SENSORS, COMMAND_SCAN_MCP23017):
        bus.send_config(mid, cmd)
        time.sleep(_CMD_GAP_S)

    engine.read_gpio_roles_from_module(summary=summary)

    deadline = time.time() + 3.0
    while time.time() < deadline:
        bus.pump_rx(timeout=0.05)

    detail = bus.module_detail(mid)
    return {"ok": True, "module": detail}
