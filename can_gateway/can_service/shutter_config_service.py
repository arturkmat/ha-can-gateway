"""Konfiguracja rolet — pary przekaźników i czasy jazdy."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from protocol_constants import (
    COMMAND_GET_SHUTTER_RELAYS,
    COMMAND_GET_SHUTTER_TIME_CLOSE,
    COMMAND_GET_SHUTTER_TIME_OPEN,
    COMMAND_SET_SHUTTER_RELAYS,
    COMMAND_SET_SHUTTER_TIME_CLOSE,
    COMMAND_SET_SHUTTER_TIME_OPEN,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager


def get_shutter_config(bus: BusManager, module_id: int, shutter_no: int) -> dict[str, Any]:
    sid = int(shutter_no)
    resp = bus.send_config_and_wait(module_id, COMMAND_GET_SHUTTER_RELAYS, [sid], timeout=0.6)
    if resp is None or len(resp) < 6 or int(resp[2]) != 0:
        return {"ok": False, "error": "GET_SHUTTER_RELAYS failed"}
    open_relay = int(resp[4])
    close_relay = int(resp[5])
    open_ds = close_ds = None
    ro = bus.send_config_and_wait(module_id, COMMAND_GET_SHUTTER_TIME_OPEN, [sid], timeout=0.4)
    if ro and len(ro) >= 5 and int(ro[2]) == 0:
        open_ds = int(ro[4])
    rc = bus.send_config_and_wait(module_id, COMMAND_GET_SHUTTER_TIME_CLOSE, [sid], timeout=0.4)
    if rc and len(rc) >= 5 and int(rc[2]) == 0:
        close_ds = int(rc[4])
    return {
        "ok": True,
        "module_id": int(module_id),
        "shutter_no": sid,
        "relay_open": open_relay,
        "relay_close": close_relay,
        "time_open_ds": open_ds,
        "time_close_ds": close_ds,
        "time_open_s": (open_ds / 10.0) if open_ds is not None else None,
        "time_close_s": (close_ds / 10.0) if close_ds is not None else None,
    }


def set_shutter_relays(
    bus: BusManager,
    module_id: int,
    shutter_no: int,
    relay_open: int,
    relay_close: int,
) -> dict[str, Any]:
    sid = int(shutter_no)
    ro = int(relay_open)
    rc = int(relay_close)
    resp = bus.send_config_and_wait(module_id, COMMAND_SET_SHUTTER_RELAYS, [sid, ro, rc], timeout=0.8)
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return {"ok": False, "error": "SET_SHUTTER_RELAYS failed"}
    engine = bus._get_engine()  # noqa: SLF001
    ctx = engine.context(int(module_id))
    if ro == 0 and rc == 0:
        ctx.shutter_relay_pairs.pop(sid, None)
    else:
        ctx.shutter_relay_pairs[sid] = {"up": ro, "down": rc}
    return {"ok": True, "module_id": int(module_id), "shutter_no": sid, "relay_open": ro, "relay_close": rc}


def set_shutter_times(
    bus: BusManager,
    module_id: int,
    shutter_no: int,
    *,
    time_open_s: float | None = None,
    time_close_s: float | None = None,
) -> dict[str, Any]:
    sid = int(shutter_no)
    results: dict[str, Any] = {"ok": True, "module_id": int(module_id), "shutter_no": sid}
    if time_open_s is not None:
        ds = max(1, min(2550, int(float(time_open_s) * 10)))
        resp = bus.send_config_and_wait(module_id, COMMAND_SET_SHUTTER_TIME_OPEN, [sid, ds], timeout=0.6)
        ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
        results["time_open_ds"] = ds
        results["ok"] = results["ok"] and ok
    if time_close_s is not None:
        ds = max(1, min(2550, int(float(time_close_s) * 10)))
        resp = bus.send_config_and_wait(module_id, COMMAND_SET_SHUTTER_TIME_CLOSE, [sid, ds], timeout=0.6)
        ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
        results["time_close_ds"] = ds
        results["ok"] = results["ok"] and ok
    return results


def clear_shutter(bus: BusManager, module_id: int, shutter_no: int) -> dict[str, Any]:
    return set_shutter_relays(bus, module_id, shutter_no, 0, 0)
