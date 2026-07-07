"""Shutter CONTROL_COMMAND payload and add-on routing tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

_pc_spec = importlib.util.spec_from_file_location(
    "protocol_constants",
    LIB / "protocol_constants.py",
    submodule_search_locations=[],
)
_pc_mod = importlib.util.module_from_spec(_pc_spec)
assert _pc_spec.loader is not None
_pc_spec.loader.exec_module(_pc_mod)
sys.modules["protocol_constants"] = _pc_mod

from protocol_constants import (  # noqa: E402
    SHUTTER_CMD_CLOSE,
    SHUTTER_CMD_OPEN,
    SHUTTER_CMD_SET_POSITION,
    SHUTTER_CMD_STOP,
    V2_CTRL_SHUTTER_CMD,
    build_shutter_control_payload,
    can_v2_control_command_id,
)


def test_build_shutter_control_payload_open():
    assert build_shutter_control_payload(1, SHUTTER_CMD_OPEN, 0) == [
        V2_CTRL_SHUTTER_CMD,
        1,
        SHUTTER_CMD_OPEN,
        0,
        0,
        0,
        0,
        0,
    ]


def test_build_shutter_control_payload_position_clamps_param():
    assert build_shutter_control_payload(2, SHUTTER_CMD_SET_POSITION, 150)[3] == 100
    assert build_shutter_control_payload(2, SHUTTER_CMD_CLOSE, 99)[3] == 0


def test_control_command_can_id_class_bits():
    can_id = can_v2_control_command_id(201)
    assert can_id & 0x07 == 2
    assert (can_id >> 3) & 0xFF == 201


def test_configurator_engine_uses_v3_shutter_payload(monkeypatch):
    ce_spec = importlib.util.spec_from_file_location(
        "configurator_engine",
        LIB / "configurator_engine.py",
    )
    ce_mod = importlib.util.module_from_spec(ce_spec)
    sys.modules["configurator_engine"] = ce_mod
    assert ce_spec.loader is not None
    ce_spec.loader.exec_module(ce_mod)

    sent: list[tuple[int, int, list[int]]] = []

    class _FakeIo:
        def io_acquire(self) -> None:
            return None

        def io_release(self) -> None:
            return None

        def notify(self) -> None:
            return None

        def sync_transport_macs(self) -> None:
            return None

        def recv(self, timeout: float):
            return None

    engine = ce_mod.ConfiguratorEngine(_FakeIo(), secure_can=False)
    engine.discovered_modules = [{"module_id": 201, "has_master_key": False}]
    monkeypatch.setattr(engine, "_io_acquire", lambda: None)
    monkeypatch.setattr(engine, "_io_release", lambda: None)
    monkeypatch.setattr(engine, "_refresh_secure_transport", lambda: None)
    monkeypatch.setattr(engine, "_safe_recv", lambda _t: None)
    monkeypatch.setattr(engine, "_normalize", lambda m: m)
    monkeypatch.setattr(
        engine,
        "_secure_bus_send",
        lambda target, can_id, data, **kwargs: sent.append((target, can_id, list(data))),
    )

    result = engine.set_shutter_command(201, 1, "close", 0)
    assert result["ok"] is True
    assert sent
    target, can_id, payload = sent[-1]
    assert target == 201
    assert can_id == can_v2_control_command_id(201)
    assert payload == build_shutter_control_payload(1, SHUTTER_CMD_CLOSE, 0)


def test_addon_setup_routes_control_before_config_reboot():
    addon_path = Path(__file__).resolve().parents[1] / "custom_components" / "can_gateway_v3"
    spec = importlib.util.spec_from_file_location(
        "can_gateway_v3_protocol",
        addon_path / "protocol.py",
    )
    protocol = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(protocol)

    module_id = 201
    shutter_no = 1
    can_id = protocol.can_v2_control_command_id(module_id)
    data = protocol.build_shutter_control_payload(shutter_no, protocol.SHUTTER_CMD_OPEN, 0)

    assert protocol.can_v2_frame_module_id(can_id) == module_id
    assert (can_id & 0x07) == protocol.CAN_V2_CLASS_CONTROL_COMMAND
    assert data[0] == protocol.V2_CTRL_SHUTTER_CMD
    assert data[1] == shutter_no
    assert data[2] == protocol.SHUTTER_CMD_OPEN
    # Old bug: data[0]==1 and data[1]==1 looked like reboot(module_id=1)
    assert not (
        (can_id & 0x07) == protocol.CAN_V2_CLASS_CONFIG_REQUEST
        and data[1] == 1
    )
