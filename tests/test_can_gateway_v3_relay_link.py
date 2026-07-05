"""Relay-link protocol helpers for can_gateway_v3 (commands 116–119)."""

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
protocol = _load_module("protocol", "protocol.py")
pkg.protocol = protocol


class TestCanGatewayV3RelayLink(unittest.TestCase):
    def test_pack_set_relay_link_args(self) -> None:
        args = protocol.pack_set_relay_link_args(2, 3, 5, 7, 1)
        self.assertEqual(args, [2, 3, 5, 7, 1])

    def test_unpack_get_relay_link_fields(self) -> None:
        fields = protocol.unpack_get_relay_link_fields([1, 3, 5, 7, 2])
        self.assertEqual(fields, (1, 3, 5, 7, 2))

    def test_format_relay_link_summary_mirror(self) -> None:
        text = protocol.format_relay_link_summary(1, protocol.RELAY_LINK_TRIGGER_MIRROR, 3, 4, 0)
        self.assertIn("R1", text)
        self.assertIn("mirror", text)
        self.assertIn("M3 R4", text)

    def test_format_relay_link_summary_timed(self) -> None:
        text = protocol.format_relay_link_summary(
            2,
            protocol.RELAY_LINK_TRIGGER_ON,
            1,
            3,
            protocol.BIND_RELAY_STATE_TIMED_MIN + 5,
        )
        self.assertIn("timed_5m", text)

    def test_resolve_relay_link_target_state_timed(self) -> None:
        code = protocol.resolve_relay_link_target_state(timed_minutes=10, trigger=1)
        self.assertEqual(code, protocol.BIND_RELAY_STATE_TIMED_MIN + 10)

    def test_resolve_relay_link_target_state_mirror(self) -> None:
        code = protocol.resolve_relay_link_target_state(
            target_state="on",
            trigger=protocol.RELAY_LINK_TRIGGER_MIRROR,
        )
        self.assertEqual(code, 0)

    def test_pack_set_relay_bind_route_args(self) -> None:
        args = protocol.pack_set_relay_bind_route_args(2, 1, 5, 3, 1)
        self.assertEqual(args, [2, 1, 5, 3, 1])

    def test_format_relay_bind_route_summary(self) -> None:
        text = protocol.format_relay_bind_route_summary(1, 1, 3, 2, 1)
        self.assertIn("btn1", text)
        self.assertIn("M3 R2", text)


if __name__ == "__main__":
    unittest.main()
