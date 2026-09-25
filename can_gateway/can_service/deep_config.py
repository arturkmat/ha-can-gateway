"""Głęboki odczyt konfiguracji modułu — jak zakładki konfiguratora.

Phase 2: successful GET fields replace /data cache for that module. Failed GET
keeps the previous cache and marks config_stale — never wipe the house catalog.
"""

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
        return {"ok": False, "error": "bus not open", "config_stale": True}

    engine = bus._get_engine()  # noqa: SLF001
    engine.set_current_module(mid)
    ctx = engine.context(mid)

    # Snapshot previous runtime so a failed GET cannot erase disk/cache truth.
    prev_detail = None
    try:
        prev_detail = bus.module_detail(mid)
    except Exception:  # noqa: BLE001
        prev_detail = None

    summary_ok = False
    summary = bus.send_config_and_wait(mid, COMMAND_GET_SUMMARY, timeout=1.0)
    if summary and len(summary) >= 8 and int(summary[2]) == 0:
        ctx.last_summary_response = list(summary)
        ctx.summary_details = engine.build_summary_details(summary)
        engine._apply_summary_counts(mid, summary)  # noqa: SLF001
        summary_ok = True
    else:
        summary = ctx.last_summary_response

    name_ok = False
    name = engine._read_module_name(mid)  # noqa: SLF001
    if name is not None:
        name_ok = True
        ctx.name = name
        for item in engine.discovered_modules:
            if item.get("module_id") == mid:
                item["name"] = name
                break

    for cmd in (COMMAND_GET_BUILD_INFO, COMMAND_SCAN_SENSORS, COMMAND_SCAN_MCP23017):
        bus.send_config_and_wait(mid, cmd, timeout=1.0)
        time.sleep(_CMD_GAP_S)

    engine.read_gpio_roles_from_module(summary=summary)

    deadline = time.time() + 3.0
    while time.time() < deadline:
        bus.pump_rx(timeout=0.05)

    gpio_ok = bool(getattr(ctx, "config_from_module", False))
    any_ok = summary_ok or name_ok or gpio_ok

    if any_ok:
        ctx.config_from_module = True
        ctx.config_stale = False
        detail = bus.module_detail(mid)
        return {
            "ok": True,
            "module": detail,
            "config_source": "module",
            "config_stale": False,
            "got": {
                "summary": summary_ok,
                "name": name_ok,
                "gpio_roles": gpio_ok,
            },
        }

    # GET failed — keep previous cache; do not clear house catalog fields.
    ctx.config_stale = True
    ctx.config_from_module = False
    if isinstance(prev_detail, dict):
        rt = prev_detail.get("runtime") if isinstance(prev_detail.get("runtime"), dict) else {}
        # Restore module-sourced fields from disk cache so export does not wipe them.
        gpio_roles = rt.get("gpio_roles") if isinstance(rt.get("gpio_roles"), dict) else {}
        if gpio_roles and not ctx.gpio_info:
            for key, info in gpio_roles.items():
                try:
                    gpio = int(info.get("gpio", key))
                except (TypeError, ValueError):
                    continue
                if isinstance(info, dict):
                    ctx.gpio_info[gpio] = {
                        "role": int(info.get("role", 0)),
                        "index": int(info.get("index", 0)),
                        "flags": int(info.get("flags", 0)),
                    }
        if prev_detail.get("name") and not ctx.name:
            ctx.name = str(prev_detail.get("name"))
        shutter_map = rt.get("shutter_map") if isinstance(rt.get("shutter_map"), dict) else {}
        if shutter_map and not ctx.shutter_relay_pairs:
            for key, pair in shutter_map.items():
                try:
                    sid = int(key)
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                        ctx.shutter_relay_pairs[sid] = {"up": int(pair[0]), "down": int(pair[1])}
                except (TypeError, ValueError):
                    continue
        pulses = rt.get("relay_pulse_ms") if isinstance(rt.get("relay_pulse_ms"), dict) else {}
        for key, val in pulses.items():
            try:
                ctx.relay_pulse_ms_by_index[int(key)] = int(val)
            except (TypeError, ValueError):
                continue

    detail = prev_detail if isinstance(prev_detail, dict) else bus.module_detail(mid)
    if isinstance(detail, dict):
        detail = dict(detail)
        detail["config_source"] = "stale"
        detail["config_stale"] = True
    return {
        "ok": False,
        "error": "module GET failed; cache retained",
        "module": detail,
        "config_source": "stale",
        "config_stale": True,
    }
