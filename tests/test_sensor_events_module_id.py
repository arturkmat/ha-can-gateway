"""SENSOR_EVENTS: module_id from V3 arbitration ID, not unreliable payload[0]."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

LIB = Path(__file__).resolve().parents[1] / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from configurator_engine import ConfiguratorEngine  # noqa: E402
from protocol_constants import (  # noqa: E402
    CAN_V2_CLASS_SENSOR_EVENTS,
    can_v2_frame_id,
)


class _FakeIo:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.notified = 0

    def bus_ok(self) -> bool:
        return True

    def io_acquire(self) -> None:
        self._lock.acquire()

    def io_release(self) -> None:
        self._lock.release()

    def recv(self, timeout: float) -> Any:
        del timeout
        return None

    def send_can_frame(self, frame_id: int, data: list[int]) -> None:
        del frame_id, data

    def normalize(self, message: Any) -> Any:
        return message

    def log(self, message: str) -> None:
        del message

    def notify(self) -> None:
        self.notified += 1

    def invalidate_transport_macs(self) -> None:
        pass

    def sync_transport_macs(self) -> None:
        pass


def _sensor_msg(module_id: int, payload: list[int]) -> SimpleNamespace:
    return SimpleNamespace(
        arbitration_id=can_v2_frame_id(CAN_V2_CLASS_SENSOR_EVENTS, module_id),
        data=bytes(payload),
        is_extended_id=False,
    )


def test_sensor_events_use_arb_module_id_when_payload0_is_zero() -> None:
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    # DS18: payload[0]=0 (legacy), sensor #1, type 1, temp bytes
    payload = [0, 1, 1, 0xE8, 0x03, 0, 0]
    assert engine.handle_can_message(_sensor_msg(3, payload)) is True
    ctx = engine.context(3)
    assert len(ctx.sensors) == 1
    assert ctx.sensors[0]["sensor_no"] == 1
    assert ctx.sensors[0]["sensor_type"] == 1
    assert engine.context(0).sensors == []


def test_sensor_events_prefer_arb_over_conflicting_payload0() -> None:
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    # sensor_idx=1 in payload[0] must not attribute frame to module 1 when CAN ID is module 5
    payload = [1, 1, 1, 0x10, 0x27, 0, 0]
    assert engine.handle_can_message(_sensor_msg(5, payload)) is True
    assert len(engine.context(5).sensors) == 1
    assert engine.context(1).sensors == []
