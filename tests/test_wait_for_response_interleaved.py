"""Unmatched CONFIG responses must be applied, not dropped, during wait_for_response."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

LIB = Path(__file__).resolve().parents[1] / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from configurator_engine import ConfiguratorEngine  # noqa: E402
from protocol_constants import (  # noqa: E402
    COMMAND_GET_GPIO_ROLE,
    COMMAND_SCAN_SENSORS,
    COMMAND_SET_RELAY_STATE,
    can_v2_config_response_id,
)


class _Msg:
    def __init__(self, arb: int, data: list[int]) -> None:
        self.arbitration_id = arb
        self.data = bytes(data)


class _FakeIo:
    def __init__(self, inbox: list[_Msg]) -> None:
        self._lock = threading.Lock()
        self._inbox = list(inbox)
        self.sent: list[tuple[int, list[int]]] = []

    def bus_ok(self) -> bool:
        return True

    def io_acquire(self) -> None:
        self._lock.acquire()

    def io_release(self) -> None:
        self._lock.release()

    def recv(self, timeout: float) -> Any:
        del timeout
        if self._inbox:
            return self._inbox.pop(0)
        return None

    def send_can_frame(self, frame_id: int, data: list[int]) -> None:
        self.sent.append((frame_id, list(data)))

    def normalize(self, message: Any) -> Any:
        return message

    def log(self, message: str) -> None:
        del message

    def notify(self) -> None:
        pass

    def invalidate_transport_macs(self) -> None:
        pass

    def sync_transport_macs(self) -> None:
        pass


def test_wait_for_response_applies_interleaved_scan_sensors() -> None:
    mid = 42
    scan_ack = _Msg(
        can_v2_config_response_id(mid),
        [mid, COMMAND_SCAN_SENSORS, 0, 0x01, 0x80, 0, 0, 0],
    )
    gpio_ack = _Msg(
        can_v2_config_response_id(mid),
        [mid, COMMAND_GET_GPIO_ROLE, 0, 2, 1, 0, 5, 0],
    )
    io = _FakeIo([scan_ack, gpio_ack])
    engine = ConfiguratorEngine(io)
    engine.set_current_module(mid)

    resp = engine.wait_for_response(mid, COMMAND_GET_GPIO_ROLE, timeout=0.2, log_traffic=False)
    assert resp is not None
    assert resp[1] == COMMAND_GET_GPIO_ROLE
    assert engine.context(mid).sensor_scan is not None
    assert engine.context(mid).sensor_scan["flags"] == 0x01


def test_set_relay_still_requires_ack_not_cache() -> None:
    mid = 5
    io = _FakeIo([])  # no ACK on the wire
    engine = ConfiguratorEngine(io)
    engine.context(mid).virtual_relay_values[17] = 1  # stale 0x600 cache
    # Bypass nested acquire for unit test: call set_relay which uses send_request → wait
    result = engine.set_relay_state(mid, 17, "on")
    assert result["ok"] is False
    assert result.get("error") == "no response"
    assert io.sent, "SET_RELAY must still transmit the CONFIG frame"
    _frame_id, payload = io.sent[0]
    assert payload[1] == COMMAND_SET_RELAY_STATE
    assert payload[2] == 17
    assert payload[3] == 1
