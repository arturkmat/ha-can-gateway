"""SET_RELAY must not succeed without a CONFIG response ACK (regression for HA false 200)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

LIB = Path(__file__).resolve().parents[1] / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from configurator_engine import ConfiguratorEngine  # noqa: E402
from protocol_constants import COMMAND_SET_RELAY_STATE  # noqa: E402


class _FakeIo:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: list[list[int] | None] = []

    def queue_response(self, payload: list[int] | None) -> None:
        self._responses.append(payload)

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
        pass

    def invalidate_transport_macs(self) -> None:
        pass

    def sync_transport_macs(self) -> None:
        pass


def test_set_relay_fails_without_ack_even_if_telemetry_cache_has_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    io = _FakeIo()
    io.queue_response(None)
    engine = ConfiguratorEngine(io)
    ctx = engine.context(5)
    ctx.virtual_relay_values[2] = 1

    monkeypatch.setattr(
        engine,
        "send_request",
        lambda *a, **k: io._responses.pop(0) if io._responses else None,
    )

    result = engine.set_relay_state(5, 2, "on")
    assert result["ok"] is False
    assert result.get("error") == "no response"


def test_set_relay_succeeds_on_config_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    ack = [5, COMMAND_SET_RELAY_STATE, 0, 2, 1, 0, 0, 0]

    monkeypatch.setattr(engine, "send_request", lambda *a, **k: ack)

    result = engine.set_relay_state(5, 2, "on")
    assert result["ok"] is True
    assert result["on"] is True
