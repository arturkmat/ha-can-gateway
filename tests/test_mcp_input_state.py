"""Regression tests for MCP23017 live input-state polling in ConfiguratorEngine.

History:
- COMMAND_GET_MCP23017_INPUT_STATE was originally polled from a separate,
  redundant scan in bus_manager.py (ensure_relay_metadata()), sent WITHOUT the
  chip_idx arg that ROLE_DUMP requires -- the firmware answered a uniform
  status=2 error for every module regardless of whether it had a chip.
- That separate scan also wrote to BusManager's own self._modules registry,
  which nothing else populates in this deployment (module tracking lives
  entirely in ConfiguratorEngine's ModuleContext/_contexts) -- so even after
  fixing the chip arg, the correctly-read value had nowhere to surface in
  module_detail()/API responses, and it duplicated CAN traffic against the
  same module already being scanned by the engine's own
  read_gpio_roles_from_module() (COMMAND_SCAN_MCP23017 found_mask + per-chip
  ROLE_DUMP), which is the actually-used path.
- Fix: read_gpio_roles_from_module() now also polls INPUT_STATE per chip
  (same found_mask loop that already fetches ROLE_DUMP), storing into
  ctx.mcp_input_state, which export_module_dict() serializes into
  runtime["mcp_input_state"] alongside mcp_relay_pins/mcp_pin_roles -- the
  same live-state path already proven to work for module 121's MCP23017.
  The redundant bus_manager.py-side scan was removed entirely.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

LIB = Path(__file__).resolve().parents[1] / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _load_configurator_engine():
    for name in ("protocol_constants", "pinout_data"):
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, LIB / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = mod
        spec.loader.exec_module(mod)

    spec = importlib.util.spec_from_file_location("configurator_engine", LIB / "configurator_engine.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["configurator_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_engine_with_mcp_module(module_id: int):
    ce = _load_configurator_engine()
    io = MagicMock()
    io.bus_ok.return_value = True
    engine = ce.ConfiguratorEngine(io)
    engine.set_current_module(module_id)
    ctx = engine.context(module_id)
    ctx.hw_flags = 0x08  # bit3 = has MCP23017 expander (matches module 121's mcp=0x26)
    engine.get_all_gpio_roles = lambda: None  # isolate MCP scanning from the GPIO-role scan
    return ce, engine, ctx


def test_read_gpio_roles_polls_input_state_per_found_chip_with_chip_arg():
    """COMMAND_GET_MCP23017_INPUT_STATE must be sent with args=[chip] for every
    chip found_mask reports present -- exactly like ROLE_DUMP already does.
    Omitting the arg is what made the firmware answer status=2 for every
    module regardless of whether it actually had a chip."""
    ce, engine, ctx = _make_engine_with_mcp_module(121)

    calls: list[tuple[int, int, list[int] | None]] = []

    def fake_send_request(target_id, command, args=None, *, timeout=1.0, log_traffic=True, bypass_config_lock=False):
        calls.append((target_id, command, args))
        if command == ce.COMMAND_SCAN_MCP23017:
            return [121, 67, 0, 0x40, 0, 0, 0, 0]  # found_mask=0x40 -> chip 6 only
        if command == ce.COMMAND_GET_MCP23017_ROLE_DUMP:
            return [121, 70, 0, args[0], 0, 0, 0, 0]
        if command == ce.COMMAND_GET_MCP23017_INPUT_STATE:
            assert args == [6], f"expected chip arg [6], got {args!r}"
            return [121, 68, 0, 255, 191, 0, 0, 0]
        return None

    engine.send_request = fake_send_request
    engine.read_gpio_roles_from_module(summary=[121, 0, 0, 4, 0, 0, 0, 0x08])

    input_state_calls = [c for c in calls if c[1] == ce.COMMAND_GET_MCP23017_INPUT_STATE]
    assert input_state_calls, "COMMAND_GET_MCP23017_INPUT_STATE was never sent"
    assert input_state_calls[0][2] == [6]


def test_read_gpio_roles_stores_mcp_input_state_in_context():
    ce, engine, ctx = _make_engine_with_mcp_module(121)

    def fake_send_request(target_id, command, args=None, *, timeout=1.0, log_traffic=True, bypass_config_lock=False):
        if command == ce.COMMAND_SCAN_MCP23017:
            return [121, 67, 0, 0x40, 0, 0, 0, 0]
        if command == ce.COMMAND_GET_MCP23017_ROLE_DUMP:
            return [121, 70, 0, args[0], 0, 0, 0, 0]
        if command == ce.COMMAND_GET_MCP23017_INPUT_STATE:
            return [121, 68, 0, 255, 191, 0, 0, 0]  # status=0, gpa=255, gpb=191
        return None

    engine.send_request = fake_send_request
    engine.read_gpio_roles_from_module(summary=[121, 0, 0, 4, 0, 0, 0, 0x08])

    assert ctx.mcp_input_state == {6: {"gpa": 255, "gpb": 191}}


def test_read_gpio_roles_skips_chip_on_error_status():
    ce, engine, ctx = _make_engine_with_mcp_module(121)
    ctx.mcp_input_state = {6: {"gpa": 5, "gpb": 0}}  # pre-existing good value

    def fake_send_request(target_id, command, args=None, *, timeout=1.0, log_traffic=True, bypass_config_lock=False):
        if command == ce.COMMAND_SCAN_MCP23017:
            return [121, 67, 0, 0x40, 0, 0, 0, 0]
        if command == ce.COMMAND_GET_MCP23017_ROLE_DUMP:
            return [121, 70, 0, args[0], 0, 0, 0, 0]
        if command == ce.COMMAND_GET_MCP23017_INPUT_STATE:
            return [121, 68, 2, 0, 0, 0, 0, 0]  # status=2 (error), as seen live pre-fix
        return None

    engine.send_request = fake_send_request
    engine.read_gpio_roles_from_module(summary=[121, 0, 0, 4, 0, 0, 0, 0x08])

    # An error response for this pass must not silently zero out previously
    # known-good state for the chip.
    assert ctx.mcp_input_state == {6: {"gpa": 5, "gpb": 0}}


def test_export_module_dict_includes_mcp_input_state():
    """This is what module_detail()/api/entities ultimately reads -- without
    it in the exported runtime dict, entity_export._mcp_pin_value() has
    nothing to resolve binary_sensor/sensor states from, and MCP-wired
    entities stay 'unknown' in HA even though the bus read succeeded."""
    ce, engine, ctx = _make_engine_with_mcp_module(121)
    ctx.mcp_input_state = {6: {"gpa": 255, "gpb": 191}}

    out = engine.export_module_dict(121)

    assert out["runtime"]["mcp_input_state"] == {"6": {"gpa": 255, "gpb": 191}}
