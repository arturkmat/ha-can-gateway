"""Regression test for MCP23017 input-state polling (ensure_relay_metadata).

Bug: COMMAND_GET_MCP23017_INPUT_STATE was sent without the chip_idx arg that
ROLE_DUMP requires, so the firmware answered with a uniform status=2 error for
every module (with or without an actual MCP23017 chip) and mcp_input_state
was never populated -> module 121's MCP binary_sensor entities stayed
"unknown" forever. Fix: query once per known chip with args=[chip], like
ROLE_DUMP already does, and read gpa/gpb from the correct response offsets.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "can_gateway" / "can_service"
LIB = ROOT / "can_gateway" / "lib"
for path in (SERVICE, LIB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_bus_manager():
    pkg = types.ModuleType("can_service")
    pkg.__path__ = [str(SERVICE)]
    sys.modules["can_service"] = pkg

    for name in ("can_send", "configurator_bridge", "options", "module_store"):
        spec = importlib.util.spec_from_file_location(
            f"can_service.{name}",
            SERVICE / f"{name}.py",
            submodule_search_locations=[str(SERVICE)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "can_service"
        assert spec.loader is not None
        sys.modules[f"can_service.{name}"] = mod
        spec.loader.exec_module(mod)

    spec = importlib.util.spec_from_file_location(
        "can_service.bus_manager",
        SERVICE / "bus_manager.py",
        submodule_search_locations=[str(SERVICE)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "can_service"
    assert spec.loader is not None
    sys.modules["can_service.bus_manager"] = mod
    spec.loader.exec_module(mod)
    return mod.BusManager, mod.ModuleRecord, sys.modules["can_service.options"].AddonOptions


def _make_bus_with_module(module_id: int, *, known_chip: int, hw_flags: int = 0x08):
    BusManager, ModuleRecord, AddonOptions = _load_bus_manager()
    options = AddonOptions(
        can_interface="slcan",
        can_port="/dev/ttyACM0",
        can_bitrate=125000,
        tty_baudrate=115200,
        auto_scan=False,
        auto_scan_interval_s=10,
    )
    bus = BusManager(options)
    rec = ModuleRecord(module_id=module_id)
    rec.runtime.hw_flags = hw_flags
    rec.runtime.mcp_relay_pins = {known_chip: set()}
    bus._modules[module_id] = rec
    return bus, rec


def test_ensure_relay_metadata_sends_chip_arg_for_input_state():
    """COMMAND_GET_MCP23017_INPUT_STATE must be sent with args=[chip], exactly
    like COMMAND_GET_MCP23017_ROLE_DUMP -- omitting it is what made the
    firmware answer with a generic error for every module."""
    bus, _rec = _make_bus_with_module(121, known_chip=6)

    calls: list[tuple[int, int, list[int] | None]] = []

    def fake_send(mid, command, args=None, *, timeout=1.0):
        calls.append((mid, command, args))
        from protocol_constants import COMMAND_GET_MCP23017_INPUT_STATE, COMMAND_GET_MCP23017_ROLE_DUMP

        if command == COMMAND_GET_MCP23017_ROLE_DUMP:
            return [121, 70, 0, 6, 0, 0, 0, 0]
        if command == COMMAND_GET_MCP23017_INPUT_STATE:
            # status=0 (OK), gpa=0x05, gpb=0x00
            return [121, 68, 0, 5, 0, 0, 0, 0]
        return None

    bus.send_config_and_wait = fake_send

    bus.ensure_relay_metadata(121)

    from protocol_constants import COMMAND_GET_MCP23017_INPUT_STATE

    input_state_calls = [c for c in calls if c[1] == COMMAND_GET_MCP23017_INPUT_STATE]
    assert input_state_calls, "COMMAND_GET_MCP23017_INPUT_STATE was never sent"
    assert input_state_calls[0][2] == [6], (
        f"expected chip arg [6] on COMMAND_GET_MCP23017_INPUT_STATE, got {input_state_calls[0][2]!r} "
        "-- without it the firmware cannot tell which chip to answer for"
    )


def test_ensure_relay_metadata_populates_mcp_input_state_per_chip():
    bus, rec = _make_bus_with_module(121, known_chip=6)

    def fake_send(mid, command, args=None, *, timeout=1.0):
        from protocol_constants import COMMAND_GET_MCP23017_INPUT_STATE, COMMAND_GET_MCP23017_ROLE_DUMP

        if command == COMMAND_GET_MCP23017_ROLE_DUMP:
            return [121, 70, 0, 6, 0, 0, 0, 0]
        if command == COMMAND_GET_MCP23017_INPUT_STATE:
            assert args == [6]
            return [121, 68, 0, 5, 0, 0, 0, 0]  # status=0, gpa=5, gpb=0
        return None

    bus.send_config_and_wait = fake_send
    bus.ensure_relay_metadata(121)

    assert rec.runtime.mcp_input_state == {"6": {"gpa": 5, "gpb": 0}}


def test_ensure_relay_metadata_leaves_state_untouched_on_error_status():
    """A firmware error response (status != 0) must not overwrite good data
    with zeros -- it should simply be skipped for that chip."""
    bus, rec = _make_bus_with_module(121, known_chip=6)
    rec.runtime.mcp_input_state = {"6": {"gpa": 5, "gpb": 0}}

    def fake_send(mid, command, args=None, *, timeout=1.0):
        from protocol_constants import COMMAND_GET_MCP23017_INPUT_STATE, COMMAND_GET_MCP23017_ROLE_DUMP

        if command == COMMAND_GET_MCP23017_ROLE_DUMP:
            return [121, 70, 0, 6, 0, 0, 0, 0]
        if command == COMMAND_GET_MCP23017_INPUT_STATE:
            return [121, 68, 2, 0, 0, 0, 0, 0]  # status=2 (error), as seen live pre-fix
        return None

    bus.send_config_and_wait = fake_send
    bus.ensure_relay_metadata(121)

    # new_input_state stayed empty (all chips errored) -> existing dict is left as-is
    assert rec.runtime.mcp_input_state == {"6": {"gpa": 5, "gpb": 0}}
