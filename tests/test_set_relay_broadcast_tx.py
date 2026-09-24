"""SET_RELAY CONFIG TX must use broadcast 0x7F8 (Windows configurator parity)."""

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
    COMMAND_SET_RELAY_STATE,
    can_v2_config_request_id,
    can_v2_config_request_id_for_command,
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


def test_config_request_id_for_command_is_broadcast() -> None:
    assert can_v2_config_request_id_for_command(5, COMMAND_SET_RELAY_STATE) == can_v2_config_request_id(0xFF)
    assert can_v2_config_request_id_for_command(5, COMMAND_SET_RELAY_STATE) == 0x7F8
    # Unicast would be wrong for PC→module CONFIG on hubs / ESP32-C6 dual filter.
    assert can_v2_config_request_id(5) == 0x028


def test_set_relay_sends_broadcast_config_and_accepts_ack() -> None:
    mid = 5
    rn = 17
    ack = _Msg(
        can_v2_config_response_id(mid),
        [mid, COMMAND_SET_RELAY_STATE, 0, rn, 1, 0, 0, 0],
    )
    io = _FakeIo([ack])
    engine = ConfiguratorEngine(io)

    result = engine.set_relay_state(mid, rn, "on")

    assert result["ok"] is True
    assert result["on"] is True
    assert io.sent, "SET_RELAY must transmit"
    frame_id, payload = io.sent[0]
    assert frame_id == 0x7F8, f"expected broadcast CONFIG 0x7F8, got 0x{frame_id:03X}"
    assert payload == [mid, COMMAND_SET_RELAY_STATE, rn, 1, 0, 0, 0, 0]
