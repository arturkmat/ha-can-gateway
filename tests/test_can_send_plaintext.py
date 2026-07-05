"""Tests for plaintext CONFIG TX when secure_can=false (V3 firmware)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAN_SERVICE = ROOT / "can_gateway" / "can_service"
LIB = ROOT / "can_gateway" / "lib"
for path in (str(CAN_SERVICE), str(LIB)):
    if path not in sys.path:
        sys.path.insert(0, path)

from can_send import prepare_outgoing_frames  # noqa: E402
from protocol_constants import (  # noqa: E402
    COMMAND_GET_BUILD_INFO,
    COMMAND_GET_GPIO_VALUE,
    COMMAND_GET_MODULE_NAME,
    COMMAND_GET_SUMMARY,
    can_v2_config_request_id,
)


def test_plaintext_config_not_blocked_without_master_key():
    module_id = 1
    can_id = can_v2_config_request_id(module_id)
    for command in (
        COMMAND_GET_SUMMARY,
        COMMAND_GET_MODULE_NAME,
        COMMAND_GET_BUILD_INFO,
        COMMAND_GET_GPIO_VALUE,
    ):
        data = [module_id, command, 0, 0, 0, 0, 0, 0]
        frames = prepare_outgoing_frames(
            None,
            module_id,
            can_id,
            data,
            module_has_master_key=True,
            secure_can=False,
        )
        assert frames == [(can_id, data)]


def test_secure_mode_blocks_unknown_config_without_transport():
    module_id = 2
    can_id = can_v2_config_request_id(module_id)
    data = [module_id, COMMAND_GET_GPIO_VALUE, 7, 0, 0, 0, 0, 0]
    frames = prepare_outgoing_frames(
        None,
        module_id,
        can_id,
        data,
        module_has_master_key=True,
        secure_can=True,
    )
    assert frames is None


def test_secure_mode_allows_discovery_summary_without_transport():
    module_id = 3
    can_id = can_v2_config_request_id(module_id)
    data = [module_id, COMMAND_GET_SUMMARY, 0, 0, 0, 0, 0, 0]
    frames = prepare_outgoing_frames(
        None,
        module_id,
        can_id,
        data,
        module_has_master_key=True,
        secure_can=True,
    )
    assert frames == [(can_id, data)]
