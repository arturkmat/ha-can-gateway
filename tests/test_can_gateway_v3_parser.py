"""Basic parser tests for can_gateway_v3 (no Home Assistant runtime required)."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "can_gateway_v3"


def _load_module(name: str, filename: str, package: str = "can_gateway_v3"):
    full_name = f"{package}.{name}" if package else name
    spec = importlib.util.spec_from_file_location(full_name, PKG / filename)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package
    sys.modules[full_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


pkg = types.ModuleType("can_gateway_v3")
sys.modules["can_gateway_v3"] = pkg

const = _load_module("const", "const.py")
protocol = _load_module("protocol", "protocol.py")
led_protocol = _load_module("led_protocol", "led_protocol.py")
pkg.const = const
pkg.protocol = protocol
pkg.led_protocol = led_protocol

parser = _load_module("parser", "parser.py")
pkg.parser = parser


class TestCanGatewayV3Parser(unittest.TestCase):
    def test_v3_relay_state_telemetry(self) -> None:
        module_id = 5
        can_id = protocol.can_v2_frame_id(protocol.CAN_V2_CLASS_STATE_TELEMETRY, module_id)
        data = [protocol.TELE_RELAY_STATE, 0b00000011, 0]
        events = parser.decode_frame(can_id, data)
        types = [e[0] for e in events]
        self.assertIn(const.EVENT_RELAY, types)

    def test_v3_device_info(self) -> None:
        module_id = 2
        can_id = protocol.can_v2_frame_id(protocol.CAN_V2_CLASS_STATE_TELEMETRY, module_id)
        data = [protocol.TELE_DEVICE_INFO, 2, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF]
        events = parser.decode_frame(can_id, data)
        info = next(e for e in events if e[0] == const.EVENT_DEVICE_INFO)
        self.assertEqual(info[1]["module_id"], module_id)
        self.assertEqual(info[1]["mac"], "AA:BB:CC:DD:EE:FF")

    def test_v3_diagnostics(self) -> None:
        module_id = 3
        can_id = protocol.can_v2_frame_id(protocol.CAN_V2_CLASS_STATE_TELEMETRY, module_id)
        data = [protocol.TELE_DIAGNOSTICS, 1, 1, 26, 7, 5, 14, 30]
        events = parser.decode_frame(can_id, data)
        diag = next(e for e in events if e[0] == const.EVENT_DIAG)
        self.assertEqual(diag[1]["fw_major"], 1)
        self.assertEqual(diag[1]["build_year"], 2026)

    def test_v3_button_broadcast(self) -> None:
        can_id = protocol.can_v2_frame_id(protocol.CAN_V2_CLASS_INPUT_EVENTS, 0xFF)
        data = [1, 4, 2, 1]
        events = parser.decode_frame(can_id, data)
        btn = next(e for e in events if e[0] == const.EVENT_BUTTON)
        self.assertEqual(btn[1]["module_id"], 4)
        self.assertEqual(btn[1]["button_no"], 2)

    def test_v3_ds18b20_sensor(self) -> None:
        module_id = 7
        can_id = protocol.can_v2_frame_id(protocol.CAN_V2_CLASS_SENSOR_EVENTS, module_id)
        temp_centi = int(21.5 * 100)
        data = [module_id, 1, 1, *temp_centi.to_bytes(4, "little", signed=True)]
        events = parser.decode_frame(can_id, data)
        sensor = next(e for e in events if e[0] == const.EVENT_SENSOR)
        self.assertAlmostEqual(sensor[1]["temperature_c"], 21.5)

    def test_events_from_json_payload(self) -> None:
        can_id = protocol.can_v2_frame_id(protocol.CAN_V2_CLASS_INPUT_EVENTS, 0xFF)
        payload = f'{{"id": {can_id}, "dlc": 4, "data": [1, 4, 2, 1]}}'
        events = parser.events_from_payload(payload)
        self.assertTrue(any(e[0] == const.EVENT_FRAME for e in events))
        self.assertTrue(any(e[0] == const.EVENT_BUTTON for e in events))

    def test_led_strip_config_response_decode(self) -> None:
        rgb332 = led_protocol.rgb332_pack(255, 128, 64)
        response_data = [14, 64, 128, 2, rgb332]
        decoded = parser._decode_config_response_data(110, response_data)
        self.assertEqual(decoded["gpio"], 14)
        self.assertEqual(decoded["count"], 64)

    def test_relay_link_count_response_decode(self) -> None:
        decoded = parser._decode_config_response_data(118, [2, 16])
        self.assertEqual(decoded["link_count"], 2)
        self.assertEqual(decoded["link_max"], 16)

    def test_led_binding_count_response_decode(self) -> None:
        decoded = parser._decode_config_response_data(114, [3, 32])
        self.assertEqual(decoded["binding_count"], 3)
        self.assertEqual(decoded["binding_max"], 32)


if __name__ == "__main__":
    unittest.main()
