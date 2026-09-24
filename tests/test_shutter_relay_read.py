"""GET_SHUTTER_RELAYS deep-read retries when the bus is busy during scan."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

LIB = Path(__file__).resolve().parents[1] / "can_gateway" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from configurator_engine import ConfiguratorEngine  # noqa: E402
from protocol_constants import COMMAND_GET_SHUTTER_RELAYS, COMMAND_GET_SUMMARY  # noqa: E402


def test_read_gpio_roles_retries_shutter_relays_after_first_pass_empty() -> None:
    io = MagicMock()
    io.bus_ok.return_value = True
    engine = ConfiguratorEngine(io)
    mid = 101
    engine.set_current_module(mid)
    ctx = engine.context(mid)
    ctx.shutter_count = 1
    summary = [101, COMMAND_GET_SUMMARY, 0, 0, 8, 0, 1, 0x10, 0]

    calls: list[float] = []
    pass_index = 0

    def fake_send_request(target_id, command, args=None, *, timeout=1.0, log_traffic=True, bypass_config_lock=False):
        nonlocal pass_index
        del target_id, log_traffic, bypass_config_lock
        if command != COMMAND_GET_SHUTTER_RELAYS:
            return None
        calls.append(timeout)
        shutter_num = int(args[0]) if args else 0
        if shutter_num == 1 and pass_index == 0:
            pass_index += 1
            return None
        if shutter_num == 1:
            return [101, COMMAND_GET_SHUTTER_RELAYS, 0, 1, 20, 22, 0, 0]
        return None

    engine.get_all_gpio_roles = lambda: None  # type: ignore[method-assign]
    engine.send_request = fake_send_request  # type: ignore[method-assign]

    engine.read_gpio_roles_from_module(summary=summary)

    assert 0.35 in calls and 0.75 in calls
    assert ctx.shutter_relay_pairs[1] == {"up": 20, "down": 22}


def test_read_gpio_roles_keeps_existing_pair_when_retry_times_out() -> None:
    io = MagicMock()
    io.bus_ok.return_value = True
    engine = ConfiguratorEngine(io)
    mid = 55
    engine.set_current_module(mid)
    ctx = engine.context(mid)
    ctx.shutter_count = 1
    ctx.shutter_relay_pairs[1] = {"up": 17, "down": 18}
    summary = [55, COMMAND_GET_SUMMARY, 0, 0, 8, 0, 1, 0x10, 0]

    def fake_send_request(target_id, command, args=None, *, timeout=1.0, log_traffic=True, bypass_config_lock=False):
        del target_id, args, timeout, log_traffic, bypass_config_lock
        if command == COMMAND_GET_SHUTTER_RELAYS:
            return None
        return None

    engine.get_all_gpio_roles = lambda: None  # type: ignore[method-assign]
    engine.send_request = fake_send_request  # type: ignore[method-assign]

    engine.read_gpio_roles_from_module(summary=summary)

    assert ctx.shutter_relay_pairs[1] == {"up": 17, "down": 18}


def test_read_gpio_roles_polls_only_summary_shutter_count() -> None:
    io = MagicMock()
    io.bus_ok.return_value = True
    engine = ConfiguratorEngine(io)
    mid = 9
    engine.set_current_module(mid)
    ctx = engine.context(mid)
    summary = [9, COMMAND_GET_SUMMARY, 0, 0, 4, 0, 2, 0x00, 0]
    polled: list[int] = []

    def fake_send_request(target_id, command, args=None, *, timeout=1.0, log_traffic=True, bypass_config_lock=False):
        del target_id, timeout, log_traffic, bypass_config_lock
        if command != COMMAND_GET_SHUTTER_RELAYS:
            return None
        shutter_num = int(args[0]) if args else 0
        polled.append(shutter_num)
        if shutter_num in (1, 2):
            return [
                9,
                COMMAND_GET_SHUTTER_RELAYS,
                0,
                shutter_num,
                17 + (shutter_num - 1) * 2,
                18 + (shutter_num - 1) * 2,
                0,
                0,
            ]
        return None

    engine.get_all_gpio_roles = lambda: None  # type: ignore[method-assign]
    engine.send_request = fake_send_request  # type: ignore[method-assign]

    engine.read_gpio_roles_from_module(summary=summary)

    assert polled == [1, 2]
    assert ctx.shutter_relay_pairs[1] == {"up": 17, "down": 18}
    assert ctx.shutter_relay_pairs[2] == {"up": 19, "down": 20}
