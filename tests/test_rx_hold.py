"""Frames pulled by the RX thread during a command must reach the waiter."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "can_gateway"
LIB = ROOT / "lib"
for entry in (str(ROOT), str(LIB)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from can_service.bus_manager import BusManager  # noqa: E402
from can_service.options import AddonOptions  # noqa: E402


def test_rx_thread_frame_is_held_for_command_waiter() -> None:
    bus = BusManager(
        AddonOptions(
            can_interface="slcan",
            can_port="/dev/ttyAMA0",
            can_bitrate=125000,
            tty_baudrate=460800,
            auto_scan=False,
            auto_scan_interval_s=10,
        )
    )
    bus._rx_enabled.clear()
    frame = object()
    bus._handle_message(frame)
    assert bus._recv(0.01) is frame
