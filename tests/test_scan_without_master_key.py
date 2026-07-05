"""Tests for scan_modules_sync graceful behavior without MASTER_KEY."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from configurator_engine import ConfiguratorEngine  # noqa: E402


class _FakeIo:
    def __init__(self) -> None:
        self._depth = 0

    def bus_ok(self) -> bool:
        return True

    def recv(self, timeout: float):
        del timeout
        return None

    def send_can_frame(self, can_id: int, data: list[int]) -> None:
        del can_id, data

    def bus_send(self, target_module_id: int, can_id: int, data: list[int], *, log_traffic: bool = True) -> None:
        self.send_can_frame(can_id, data)

    def normalize(self, message):
        return message

    def io_acquire(self) -> None:
        self._depth += 1

    def io_release(self) -> None:
        self._depth = max(0, self._depth - 1)

    def log(self, message: str) -> None:
        del message

    def notify(self) -> None:
        pass

    def invalidate_transport_macs(self) -> None:
        pass

    def sync_transport_macs(self) -> None:
        pass

    def prepare_outgoing_frames(self, target_module_id: int, can_id: int, data: list[int]):
        del target_module_id, can_id, data
        return None


def test_scan_without_master_key_skips_active_relay_read(monkeypatch):
    io = _FakeIo()
    engine = ConfiguratorEngine(io, master_key=None)
    active_calls: list[bool] = []

    def _fake_refresh(*, passive_timeout_s: float = 1.2, active: bool = True):
        del passive_timeout_s
        active_calls.append(active)

    monkeypatch.setattr(engine, "refresh_all_module_relay_states", _fake_refresh)
    engine.refresh_all_module_relay_states(passive_timeout_s=1.5, active=False)
    assert active_calls == [False]


def test_scan_active_relay_read_when_master_key_present(monkeypatch):
    io = _FakeIo()
    master = bytes(range(32))
    engine = ConfiguratorEngine(io, master_key=master)
    active_calls: list[bool] = []

    def _fake_refresh(*, passive_timeout_s: float = 1.2, active: bool = True):
        del passive_timeout_s
        active_calls.append(active)

    monkeypatch.setattr(engine, "refresh_all_module_relay_states", _fake_refresh)
    engine.refresh_all_module_relay_states(passive_timeout_s=1.5, active=True)
    assert active_calls == [True]
