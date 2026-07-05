"""LED binding read helpers for can_gateway_v3 (commands 114–115)."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "can_gateway_v3"


def _load_module(name: str, filename: str, package: str = "can_gateway_v3") -> object:
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
led_protocol = _load_module("led_protocol", "led_protocol.py")
pkg.led_protocol = led_protocol
const = _load_module("const", "const.py")
protocol = _load_module("protocol", "protocol.py")
pkg.const = const
pkg.protocol = protocol
parser = _load_module("parser", "parser.py")


class TestCanGatewayV3LedBindings(unittest.TestCase):
    def test_unpack_get_led_binding_response(self) -> None:
        # effect_id=2, duration=5 → meta byte 0x2A
        payload = [3, 115, 0, 0x12, 4, 2, 1, 0x2A, 0xE0]
        data = led_protocol.unpack_get_led_binding_response(payload)
        self.assertEqual(data["strip_index"], 2)
        self.assertEqual(data["source_module"], 4)
        self.assertEqual(data["button"], 2)
        self.assertEqual(data["action"], 1)
        self.assertEqual(data["effect_id"], 2)
        self.assertEqual(data["duration_s"], 5)

    def test_format_led_binding_summary(self) -> None:
        text = led_protocol.format_led_binding_summary(
            strip_index=1,
            source_module=3,
            button=2,
            action=1,
            effect_id=led_protocol.LED_EFFECT_SOLID,
            duration_s=0,
            color_byte=led_protocol.rgb332_pack(255, 128, 0),
        )
        self.assertIn("M3 btn2", text)
        self.assertIn("solid", text)

    def test_parser_config_response_115(self) -> None:
        response_data = [0x11, 3, 1, 6, 0x09]
        decoded = parser._decode_config_response_data(115, response_data)
        self.assertEqual(decoded["strip_index"], 1)
        self.assertEqual(decoded["source_module"], 3)
        self.assertEqual(decoded["effect_id"], 1)
        self.assertEqual(decoded["duration_s"], 1)

    def test_parser_config_response_119(self) -> None:
        response_data = [2, 3, 5, 7, 1]
        decoded = parser._decode_config_response_data(119, response_data)
        self.assertEqual(decoded["src_relay"], 2)
        self.assertEqual(decoded["trigger"], 3)
        self.assertEqual(decoded["target_module"], 5)
        self.assertEqual(decoded["target_relay"], 7)

    def test_pack_set_led_binding_args(self) -> None:
        args = led_protocol.pack_set_led_binding_args(
            3, 2, 1, led_protocol.LED_EFFECT_SOLID, 5, 255, 128, 0, strip_index=2
        )
        self.assertEqual(len(args), 6)
        self.assertEqual(args[0], 3)
        self.assertEqual(args[1], 2)
        self.assertEqual(args[4], 5)

    def test_parser_config_response_88(self) -> None:
        response_data = [1, 2, 5, 3, 128 + 10]
        decoded = parser._decode_config_response_data(88, response_data)
        self.assertEqual(decoded["button"], 1)
        self.assertEqual(decoded["action"], 2)
        self.assertEqual(decoded["target_module"], 5)
        self.assertEqual(decoded["relay"], 3)
        self.assertEqual(decoded["relay_state"], 138)


if __name__ == "__main__":
    unittest.main()
