"""Binding / route decode helpers aligned with konfigurator (timed, TOF edge 5, route 93)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from protocol_constants import (  # noqa: E402
    BIND_RELAY_STATE_TIMED_MIN,
    BINARY_MAPPING_TRIGGER_LABELS,
    binary_edge_mode_from_trigger_label,
    format_binding_state_label,
    pack_set_binding_args,
    unpack_relay_state_byte,
    unpack_set_binding_arg5,
)


def test_format_binding_state_timed_and_permanent():
    assert format_binding_state_label(128 + 15) == "Czasowe 15 min"
    assert format_binding_state_label(1) == "Zalacz (permanentne)"


def test_binary_edge_tof_label_is_five():
    assert "TOF (PIR)" in BINARY_MAPPING_TRIGGER_LABELS
    assert binary_edge_mode_from_trigger_label("TOF (PIR)") == 5
    assert binary_edge_mode_from_trigger_label("Stan czujnika 1do1") == 4


def test_unpack_relay_state_byte_shutter_route_packed():
    relay, state = unpack_relay_state_byte(0x85, 0x10)
    assert relay == 5
    assert state == 2


def test_pack_set_binding_timed_sixth_byte():
    args = pack_set_binding_args(1, 2, 1, 3, 1, timed_min=20)
    assert len(args) == 6
    assert unpack_set_binding_arg5(args[5]) == 20
    assert args[4] == 1
    wire_timed = BIND_RELAY_STATE_TIMED_MIN + 20
    assert format_binding_state_label(wire_timed) == "Czasowe 20 min"
