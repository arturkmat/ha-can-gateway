"""Remaining protocol mismatch fixes: sensor 97/100 wire, cross-module button→relay 85."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "can_gateway" / "lib"
SERVICE = ROOT / "can_gateway" / "can_service"
REPO = ROOT.parent
for path in (LIB, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from protocol_constants import (  # noqa: E402
    BIND_RELAY_STATE_TIMED_MIN,
    COMMAND_GET_BINDING_COUNT,
    COMMAND_GET_BINARY_BIND_ROUTE_COUNT,
    COMMAND_GET_LED_BINDING_COUNT,
    COMMAND_GET_RELAY_BIND_ROUTE_COUNT,
    COMMAND_GET_RELAY_LINK_COUNT,
    COMMAND_GET_SENSOR_BIND_ROUTE,
    COMMAND_GET_SENSOR_BIND_ROUTE_COUNT,
    COMMAND_GET_SHUTTER_BIND_ROUTE_COUNT,
    COMMAND_GET_SHUTTER_BINDING_COUNT,
    COMMAND_SET_MAPPING,
    COMMAND_SET_RELAY_BIND_ROUTE,
    COMMAND_SET_SENSOR_BIND_ROUTE,
    pack_set_sensor_bind_route_args,
)


def _load_can_service_modules():
    pkg = types.ModuleType("can_service")
    pkg.__path__ = [str(SERVICE)]
    sys.modules["can_service"] = pkg

    def _load(name: str):
        full = f"can_service.{name}"
        if full in sys.modules:
            return sys.modules[full]
        spec = importlib.util.spec_from_file_location(full, SERVICE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "can_service"
        sys.modules[full] = mod
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        setattr(pkg, name, mod)
        return mod

    mapping_service = _load("mapping_service")
    mapping_write = _load("mapping_write_service")
    return mapping_service, mapping_write


mapping_service, mapping_write_service = _load_can_service_modules()
read_all_mappings = mapping_service.read_all_mappings
apply_button_relay_mapping = mapping_write_service.apply_button_relay_mapping
send_mappings = mapping_write_service.send_mappings


def test_pack_sensor_bind_route_97_matches_firmware_wire():
    """SET 97: packed idx/type, cmp/state, int16 LE threshold — not [kind, index, th_u8, …]."""
    args = pack_set_sensor_bind_route_args(
        sensor_idx=1,
        sensor_type=1,
        compare_mode=0,
        threshold_centi=2500,
        target_module=51,
        target_relay=3,
        relay_state=1,
    )
    assert args == [0x11, 0x10, 0xC4, 0x09, 51, 3]
    old_wrong = [1, 1, 25, 51, 3]
    assert args != old_wrong
    assert len(args) == 6


def test_pack_sensor_bind_route_rejects_timed_state():
    with pytest.raises(ValueError, match="automat czasowy"):
        pack_set_sensor_bind_route_args(1, 1, 0, 2000, 51, 2, BIND_RELAY_STATE_TIMED_MIN + 5)


def test_send_mappings_sensor_route_uses_packed_wire():
    bus = MagicMock()
    bus.send_config_and_wait.return_value = [50, COMMAND_SET_SENSOR_BIND_ROUTE, 0, 0, 0, 0, 0, 0]

    with patch.object(mapping_write_service, "read_all_mappings", return_value={"mappings": []}):
        result = send_mappings(
            bus,
            50,
            [
                {
                    "kind": "sensor_route",
                    "sensor_idx": 1,
                    "sensor_type": 1,
                    "compare_mode": 0,
                    "threshold_centi": 2500,
                    "target_module_id": 51,
                    "target_relay": 3,
                    "relay_state": 1,
                }
            ],
        )
    assert result["applied"] == 1
    calls = [
        c
        for c in bus.send_config_and_wait.call_args_list
        if c.args[1] == COMMAND_SET_SENSOR_BIND_ROUTE
    ]
    assert len(calls) == 1
    assert calls[0].args[0] == 50
    assert calls[0].args[2] == [0x11, 0x10, 0xC4, 0x09, 51, 3]


def test_get_sensor_bind_route_100_unpack_like_windows():
    """GET 100: packed index/type, cmp_state, threshold °C→hundredths, unpack_relay_state_byte."""
    mid = 50
    packed = 0x11
    cmp_state = 0x10
    th_deg = 25
    target = 51
    relay_packed = 3

    empty_counts = {
        COMMAND_GET_BINDING_COUNT,
        COMMAND_GET_SHUTTER_BINDING_COUNT,
        COMMAND_GET_RELAY_LINK_COUNT,
        COMMAND_GET_LED_BINDING_COUNT,
        COMMAND_GET_BINARY_BIND_ROUTE_COUNT,
        COMMAND_GET_SHUTTER_BIND_ROUTE_COUNT,
        COMMAND_GET_RELAY_BIND_ROUTE_COUNT,
    }

    def _wait(module_id: int, command: int, args: list[int] | None = None, timeout: float = 0.4):
        del timeout
        if command == COMMAND_GET_SENSOR_BIND_ROUTE_COUNT:
            return [module_id, command, 0, 1, 8, 0, 0, 0]
        if command == COMMAND_GET_SENSOR_BIND_ROUTE:
            assert args == [0]
            return [module_id, command, 0, packed, cmp_state, th_deg, target, relay_packed]
        if command in empty_counts:
            return [module_id, command, 0, 0, 0, 0, 0, 0]
        return [module_id, command, 0, 0, 0, 0, 0, 0]

    bus = MagicMock()
    bus.send_config_and_wait.side_effect = _wait
    bus.store_mappings = MagicMock()
    bus.list_modules.return_value = []

    result = read_all_mappings(bus, mid)
    assert result["ok"] is True
    assert len(result["mappings"]) == 1
    row = result["mappings"][0]
    assert "Temperatura" in row["button"]
    assert "1" in row["button"]
    assert "Powyzej" in row["action"]
    assert "25" in row["action"]
    assert row["target_id"] == "51"
    assert row["target"] == "3"
    assert "Zalacz" in row["state"]
    assert row["button"] != f"Sensor {packed} #{cmp_state}"
    assert not row["action"].startswith(f"> {th_deg}")


def test_apply_button_relay_cross_module_uses_opcode_85_on_source():
    bus = MagicMock()
    bus.send_config_and_wait.return_value = [50, COMMAND_SET_RELAY_BIND_ROUTE, 0, 0, 0, 0, 0, 0]
    ok = apply_button_relay_mapping(
        bus,
        source_module_id=50,
        target_module_id=51,
        button_num=2,
        action_code=1,
        relay_num=3,
        relay_state=1,
    )
    assert ok is True
    assert bus.send_config_and_wait.call_count == 1
    mid, cmd, args = bus.send_config_and_wait.call_args.args[:3]
    assert mid == 50
    assert cmd == COMMAND_SET_RELAY_BIND_ROUTE
    assert args == [2, 1, 51, 3, 1]
    assert all(c.args[1] != COMMAND_SET_MAPPING for c in bus.send_config_and_wait.call_args_list)


def test_apply_button_relay_same_module_uses_opcode_16():
    bus = MagicMock()
    bus.send_config_and_wait.return_value = [50, COMMAND_SET_MAPPING, 0, 0, 0, 0, 0, 0]
    ok = apply_button_relay_mapping(
        bus,
        source_module_id=50,
        target_module_id=50,
        button_num=1,
        action_code=1,
        relay_num=2,
        relay_state=1,
    )
    assert ok is True
    mid, cmd, args = bus.send_config_and_wait.call_args.args[:3]
    assert mid == 50
    assert cmd == COMMAND_SET_MAPPING
    assert args[:5] == [50, 1, 1, 2, 1]
    assert all(c.args[1] != COMMAND_SET_RELAY_BIND_ROUTE for c in bus.send_config_and_wait.call_args_list)
