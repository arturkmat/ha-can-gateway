"""SET_RELAY must not succeed without a CONFIG response ACK (regression for HA false 200)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

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


def test_set_relay_fails_without_ack_even_if_telemetry_cache_has_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(engine, "_wait_relay_telemetry_confirm", lambda *a, **k: False)

    result = engine.set_relay_state(5, 2, "on")
    assert result["ok"] is False
    assert result.get("error") == "no response"


def test_set_relay_succeeds_when_fresh_telemetry_matches_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    ctx = engine.context(5)
    ctx.virtual_relay_values[17] = 0

    def _send(*_a, **_k):
        ctx.virtual_relay_values[17] = 1
        ctx.relay_telemetry_gen += 1
        return None

    monkeypatch.setattr(engine, "send_request", _send)

    result = engine.set_relay_state(5, 17, "on")
    assert result["ok"] is True
    assert result["on"] is True
    assert result.get("confirmed") == "telemetry"


def test_set_relay_succeeds_when_telemetry_arrives_during_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Module may publish 0x600 after the ACK timeout; wait must catch it early."""
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    ctx = engine.context(5)
    ctx.virtual_relay_values[17] = 0
    gen_before = int(ctx.relay_telemetry_gen)

    monkeypatch.setattr(engine, "send_request", lambda *a, **k: None)

    calls = {"n": 0}

    def _wait(mid, rn, code, gen, *, timeout_s=2.5):
        del mid, code, timeout_s
        calls["n"] += 1
        assert gen == gen_before
        ctx.virtual_relay_values[rn] = 1
        ctx.relay_telemetry_gen = gen_before + 1
        return engine._relay_state_confirmed_by_telemetry(5, rn, 1, gen_before)

    monkeypatch.setattr(engine, "_wait_relay_telemetry_confirm", _wait)

    result = engine.set_relay_state(5, 17, "on")
    assert calls["n"] == 1
    assert result["ok"] is True
    assert result.get("confirmed") == "telemetry"


def test_set_relay_rejects_fresh_telemetry_that_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    ctx = engine.context(5)
    ctx.virtual_relay_values[17] = 0

    def _send(*_a, **_k):
        # Fresh frame arrived, but relay bit stayed OFF while we asked ON.
        ctx.relay_telemetry_gen += 1
        return None

    monkeypatch.setattr(engine, "send_request", _send)
    monkeypatch.setattr(engine, "_wait_relay_telemetry_confirm", lambda *a, **k: False)

    result = engine.set_relay_state(5, 17, "on")
    assert result["ok"] is False
    assert result.get("error") == "no response"


def test_wait_relay_telemetry_confirm_returns_early_on_match() -> None:
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    ctx = engine.context(5)
    gen_before = int(ctx.relay_telemetry_gen)
    ctx.virtual_relay_values[20] = 0

    class _Msg:
        def __init__(self) -> None:
            self.arbitration_id = (5 << 3) | 7  # STATE_TELEMETRY
            # TELE_RELAY_STATE subtype=2, local relays empty, HC595 bit0 = relay 17 on
            # Actually HC595 starts at 17; for simple path bump gen via apply.
            self.data = bytes([2, 0, 0, 1, 0, 0, 0, 0])

    # Inject one telemetry frame then silence.
    frames = [_Msg()]

    def _recv(timeout: float):
        del timeout
        return frames.pop(0) if frames else None

    io.recv = _recv  # type: ignore[method-assign]
    ctx.hw_flags = 0x10  # 1x HC595 register → bits in ext byte map to 17+
    ok = engine._wait_relay_telemetry_confirm(5, 17, 1, gen_before, timeout_s=0.3)
    assert ok is True
    assert int(ctx.relay_telemetry_gen) > gen_before


def test_set_relay_succeeds_on_config_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    io = _FakeIo()
    engine = ConfiguratorEngine(io)
    ack = [5, COMMAND_SET_RELAY_STATE, 0, 2, 1, 0, 0, 0]

    monkeypatch.setattr(engine, "send_request", lambda *a, **k: ack)

    result = engine.set_relay_state(5, 2, "on")
    assert result["ok"] is True
    assert result["on"] is True
