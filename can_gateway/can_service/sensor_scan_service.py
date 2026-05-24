"""Skanowanie magistral sensorów — jak zakładka Sensory w konfiguratorze."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from protocol_constants import (
    COMMAND_SCAN_1WIRE,
    COMMAND_SCAN_I2C,
    COMMAND_SCAN_MCP23017,
    COMMAND_SCAN_SENSORS,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager


def _wait_rx(bus: BusManager, timeout_s: float = 1.5) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        bus.pump_rx(min(0.08, max(0.0, deadline - time.time())))


def scan_1wire(bus: BusManager, module_id: int) -> dict[str, Any]:
    bus.clear_sensors(int(module_id))
    resp = bus.send_config_and_wait(module_id, COMMAND_SCAN_1WIRE, timeout=2.0)
    _wait_rx(bus, 1.2)
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    detail = bus.module_detail(int(module_id)) or {}
    sensors = (detail.get("runtime") or {}).get("sensors") or []
    scan = (detail.get("runtime") or {}).get("sensor_scan")
    return {"ok": ok, "module_id": int(module_id), "sensors": sensors, "sensor_scan": scan}


def scan_i2c(bus: BusManager, module_id: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_SCAN_I2C, timeout=2.0)
    _wait_rx(bus, 1.0)
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    found_mask = int(resp[4]) if resp and len(resp) > 4 else None
    return {"ok": ok, "module_id": int(module_id), "found_mask": found_mask}


def scan_sensors(bus: BusManager, module_id: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_SCAN_SENSORS, timeout=2.0)
    _wait_rx(bus, 1.5)
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    detail = bus.module_detail(int(module_id)) or {}
    rt = detail.get("runtime") or {}
    return {
        "ok": ok,
        "module_id": int(module_id),
        "sensor_scan": rt.get("sensor_scan"),
        "sensors": rt.get("sensors") or [],
    }


def scan_mcp23017(bus: BusManager, module_id: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_SCAN_MCP23017, timeout=2.0)
    _wait_rx(bus, 0.8)
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    found_mask = int(resp[3]) if resp and len(resp) > 3 else None
    return {"ok": ok, "module_id": int(module_id), "found_mask": found_mask}
