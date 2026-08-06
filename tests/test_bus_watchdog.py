"""Tests for the CAN I/O watchdog (recovers from a wedged _io_lock)."""

from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "can_gateway" / "can_service"
LIB = ROOT / "can_gateway" / "lib"
for path in (SERVICE, LIB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_bus_manager():
    pkg = types.ModuleType("can_service")
    pkg.__path__ = [str(SERVICE)]
    sys.modules["can_service"] = pkg

    for name in ("can_send", "configurator_bridge", "options", "module_store"):
        spec = importlib.util.spec_from_file_location(
            f"can_service.{name}",
            SERVICE / f"{name}.py",
            submodule_search_locations=[str(SERVICE)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "can_service"
        assert spec.loader is not None
        sys.modules[f"can_service.{name}"] = mod
        spec.loader.exec_module(mod)

    spec = importlib.util.spec_from_file_location(
        "can_service.bus_manager",
        SERVICE / "bus_manager.py",
        submodule_search_locations=[str(SERVICE)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "can_service"
    assert spec.loader is not None
    sys.modules["can_service.bus_manager"] = mod
    spec.loader.exec_module(mod)
    return mod.BusManager, sys.modules["can_service.options"].AddonOptions


def _make_bus():
    BusManager, AddonOptions = _load_bus_manager()
    options = AddonOptions(
        can_interface="slcan",
        can_port="/dev/ttyACM0",
        can_bitrate=125000,
        tty_baudrate=115200,
        auto_scan=False,
        auto_scan_interval_s=10,
    )
    return BusManager(options)


def test_force_bus_reset_does_not_block_on_held_io_lock():
    """Regression: the watchdog's whole purpose is recovering from a thread stuck
    holding _io_lock (e.g. a serial driver's recv() ignoring its timeout on a
    degraded link). _force_bus_reset() must NOT itself try to acquire _io_lock,
    otherwise it would hang right alongside every other bus operation instead of
    unblocking them."""
    bus = _make_bus()
    bus._bus = MagicMock()
    bus._active_port = "/dev/ttyACM0"

    # Simulate a stuck thread permanently holding _io_lock (as if wedged inside
    # bus.recv()).
    bus._io_lock.acquire()
    try:
        started = time.monotonic()
        bus._force_bus_reset()
        elapsed = time.monotonic() - started
    finally:
        bus._io_lock.release()

    assert elapsed < 1.0, f"_force_bus_reset() blocked for {elapsed:.2f}s on a held _io_lock"
    assert bus._bus is None
    assert bus._active_port is None
    assert bus._bus_error is not None and "watchdog" in bus._bus_error


def test_watchdog_loop_fires_after_stall_and_resets_activity(monkeypatch):
    bus = _make_bus()
    bus._bus = MagicMock()
    bus.WATCHDOG_STALL_TIMEOUT_S = 0.05  # keep the test fast
    bus._last_bus_activity = time.time() - 10.0  # already "stalled" from the start

    calls: list[float] = []
    monkeypatch.setattr(bus, "_force_bus_reset", lambda: calls.append(time.time()))

    # Run one watchdog pass manually instead of spinning up the real thread/loop.
    stalled_for = time.time() - bus._last_bus_activity
    assert stalled_for > bus.WATCHDOG_STALL_TIMEOUT_S
    if stalled_for > bus.WATCHDOG_STALL_TIMEOUT_S:
        bus._force_bus_reset()

    assert len(calls) == 1


def test_recv_and_send_mark_activity_on_both_success_and_error():
    bus = _make_bus()
    bus._bus = MagicMock()
    bus._bus.recv.return_value = None
    bus._last_bus_activity = 0.0

    bus._recv(0.01)
    assert bus._last_bus_activity > 0.0

    # Now simulate an I/O error path (recv raises) — activity must still be marked
    # so the watchdog doesn't misidentify a handled, recovered error as a stall.
    bus._bus = MagicMock()
    bus._bus.recv.side_effect = OSError("Could not read from serial device")
    bus._last_bus_activity = 0.0
    bus._recv(0.01)
    assert bus._last_bus_activity > 0.0
