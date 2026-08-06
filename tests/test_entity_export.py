"""Tests for CAN Gateway add-on entity export."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# Konfigurator tests may preload konfigurator_windows_usb_can/protocol_constants.py;
# bind HA addon lib explicitly so entity_export gets MCP23017_RELAY_CAN_BASE etc.
_pc_spec = importlib.util.spec_from_file_location(
    "protocol_constants",
    LIB / "protocol_constants.py",
    submodule_search_locations=[],
)
_pc_mod = importlib.util.module_from_spec(_pc_spec)
assert _pc_spec.loader is not None
_pc_spec.loader.exec_module(_pc_mod)
sys.modules["protocol_constants"] = _pc_mod

from entity_export import build_entities_for_module, build_entities_snapshot  # noqa: E402


def _sample_module() -> dict:
    return {
        "module_id": 201,
        "name": "Salon",
        "hw_type": 2,
        "hw_name": "XIAO ESP32-C6",
        "mac": "AA:BB:CC:DD:EE:01",
        "button_count": 2,
        "relay_count": 4,
        "shutter_count": 1,
        "runtime": {
            "hw_flags": 0,
            "relay_gpio_map": {"1": 5, "2": 6},
            "relay_pulse_ms": {"3": 250},
            "shutter_map": {"1": [1, 2]},
            "relays": [
                {"relay_no": 3, "on": False, "pulse_ms": 250, "source": "local"},
                {"relay_no": 4, "on": True, "pulse_ms": 0, "source": "local"},
            ],
            "shutters": [
                {
                    "shutter_no": 1,
                    "position": 42,
                    "direction": 0,
                    "direction_text": "stopped",
                }
            ],
            "sensor_scan": {"flags": 0x01, "ds18_gpio_or_count": 0x80},
            "sensors": [],
            "gpio_values": {
                "7": {"gpio": 7, "logical": 1, "raw": 1, "role": 4, "role_name": "BinarySensor", "valid": True},
            },
            "gpio_roles": {
                "3": {"gpio": 3, "role": 1, "role_name": "Button", "index": 1},
                "4": {"gpio": 4, "role": 1, "role_name": "Button", "index": 2},
                "5": {"gpio": 5, "role": 2, "role_name": "Relay", "index": 1},
                "6": {"gpio": 6, "role": 2, "role_name": "Relay", "index": 2},
                "9": {"gpio": 9, "role": 2, "role_name": "Relay", "index": 4},
            },
        },
        "control_relays": [
            {"relay_no": 3, "on": False, "pulse_ms": 250, "source": "local", "shutter_reserved": False},
            {"relay_no": 4, "on": True, "pulse_ms": 0, "source": "local", "shutter_reserved": False},
        ],
    }


def test_build_entities_includes_core_platforms():
    entities = build_entities_for_module(_sample_module())
    platforms = {e["platform"] for e in entities}
    uids = {e["unique_id"] for e in entities}

    assert "binary_sensor" in platforms
    assert "switch" in platforms
    assert "button" in platforms
    assert "cover" in platforms
    assert "sensor" in platforms

    assert "m201_online" in uids
    assert "m201_local_relay4" in uids
    assert "m201_local_relay3_pulse" in uids
    assert "m201_shutter1" in uids
    assert "m201_btn1_action" in uids
    assert "m201_gpio7_binary" in uids
    assert "m201_s1_ds18b20_temperature" in uids

    relay4 = next(e for e in entities if e["unique_id"] == "m201_local_relay4")
    assert relay4["value"] is True

    cover = next(e for e in entities if e["unique_id"] == "m201_shutter1")
    assert cover["value"]["position"] == 42


def test_build_entities_snapshot_deduplicates():
    mod = _sample_module()
    snapshot = build_entities_snapshot([mod, mod])
    assert len(snapshot) == len(build_entities_for_module(mod))


def test_build_entities_shutter_relays_not_exported_as_switches():
    mod = {
        "module_id": 12,
        "name": "Rolety",
        "runtime": {
            "gpio_roles": {
                "5": {"gpio": 5, "role": 2, "role_name": "Relay", "index": 1},
                "6": {"gpio": 6, "role": 2, "role_name": "Relay", "index": 2},
            },
            "relay_gpio_map": {"1": 5, "2": 6},
            "shutter_map": {"1": [1, 2]},
            "relays": [
                {"relay_no": 1, "on": True, "source": "local"},
                {"relay_no": 2, "on": False, "source": "local"},
            ],
        },
    }
    entities = build_entities_for_module(mod)
    uids = {e["unique_id"] for e in entities}
    assert "m12_shutter1" in uids
    assert "m12_local_relay1" not in uids
    assert "m12_local_relay2" not in uids


def test_build_entities_stale_summary_relay_count_ignored():
    mod = {
        "module_id": 7,
        "name": "Garaz",
        "button_count": 1,
        "relay_count": 16,
        "shutter_count": 1,
        "summary_details": "buttons=1 relays=16 ds18=0 shutters=1",
        "runtime": {
            "relays": [{"relay_no": i, "on": False, "source": "local"} for i in range(1, 17)],
        },
    }
    entities = build_entities_for_module(mod)
    uids = {e["unique_id"] for e in entities}
    assert uids == {"m7_online"}


def test_build_entities_module_name_preserved_in_module_dict():
    mod = _sample_module()
    assert mod["name"] == "Salon"
    entities = build_entities_for_module(mod)
    assert entities


def test_build_entities_from_gpio_roles_only():
    mod = {
        "module_id": 9,
        "runtime": {
            "gpio_roles": {
                "4": {"gpio": 4, "role": 1, "role_name": "Button", "index": 1},
                "5": {"gpio": 5, "role": 2, "role_name": "Relay", "index": 1},
                "6": {"gpio": 6, "role": 12, "role_name": "WS2812", "index": 1},
            },
            "relay_gpio_map": {"1": 5},
            "relays": [{"relay_no": 1, "on": False, "source": "local"}],
        },
    }
    entities = build_entities_for_module(mod)
    uids = {e["unique_id"] for e in entities}
    assert "m9_btn1_action" in uids
    assert "m9_local_relay1" in uids
    assert "m9_led_strip1" in uids
    assert "m9_local_relay2" not in uids


def test_build_entities_mcp_relay_from_role_dump():
    mod = {
        "module_id": 5,
        "runtime": {
            "hw_flags": 0x08,
            "mcp_relay_pins": {"0": [0]},
            "mcp_pin_roles": {"0": {"0": 1}},
            "relays": [{"relay_no": 57, "on": False, "source": "mcp23017"}],
        },
    }
    entities = build_entities_for_module(mod)
    uids = {e["unique_id"] for e in entities}
    assert "m5_mcp_chip0_relay57" in uids
    assert "m5_mcp_chip0_pin1_binary" not in uids


def test_build_entities_mcp_binary_pin_value_is_active_low():
    """MCP23017 inputs here are wired active-low with internal pull-ups:
    idle/not-triggered reads register bit=1, active/triggered reads bit=0.
    The exported binary_sensor value must be the inverse of the raw bit --
    True (active) only when the bit is 0."""
    mod = {
        "module_id": 121,
        "runtime": {
            "hw_flags": 0x08,
            "mcp_pin_roles": {"6": {"0": 3, "14": 3}},
            "mcp_input_state": {"6": {"gpa": 0b00000001, "gpb": 0b00000000}},  # pin0 bit=1 (idle), pin14 bit=0 (active)
        },
    }
    entities = {e["unique_id"]: e for e in build_entities_for_module(mod)}
    assert entities["m121_mcp_chip6_pin0_binary"]["value"] is False  # raw bit=1 -> idle -> off
    assert entities["m121_mcp_chip6_pin14_binary"]["value"] is True  # raw bit=0 -> active -> on


def test_build_entities_unused_mcp_pins_not_exported():
    mod = {
        "module_id": 5,
        "runtime": {
            "hw_flags": 0x08,
            "mcp_pin_roles": {"0": {"0": 1, "1": 0, "2": 0}},
            "mcp_relay_pins": {"0": [0]},
            "relays": [{"relay_no": 57, "on": False, "source": "mcp23017"}],
        },
    }
    entities = build_entities_for_module(mod)
    uids = {e["unique_id"] for e in entities}
    assert "m5_mcp_chip0_relay57" in uids
    assert "m5_mcp_chip0_relay58" not in uids
    assert "m5_mcp_chip0_pin1_binary" not in uids


def test_build_entities_hc595_relays_from_hw_flags():
    mod = {
        "module_id": 8,
        "runtime": {
            "hw_flags": 0x10,
            "relays": [{"relay_no": 17, "on": True, "source": "hc595"}],
        },
    }
    entities = build_entities_for_module(mod)
    uids = {e["unique_id"] for e in entities}
    assert "m8_hc595_relay17" in uids
    assert "m8_hc595_relay18" in uids
    assert "m8_local_relay1" not in uids
