"""CAN OTA firmware upload over SLCAN — async port of addon ota_upload_service."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

from homeassistant.core import HomeAssistant

from .can_io import CanFrameSender
from .can_request import wait_config_response, wait_ota_status
from .protocol import (
    COMMAND_OTA_ABORT,
    COMMAND_OTA_BEGIN,
    COMMAND_OTA_END,
    COMMAND_OTA_SET_TIMESTAMP,
    OTA_BATCH_FRAMES,
    OTA_PAYLOAD_BYTES,
    OTA_STATUS_DONE,
    OTA_STATUS_ERROR,
    OTA_STATUS_NACK,
    OTA_STATUS_READY,
    can_v2_config_request_id,
    can_v2_ota_data_id,
)

_LOGGER = logging.getLogger(__name__)


async def upload_firmware_over_can(
    hass: HomeAssistant,
    send_can: CanFrameSender,
    module_id: int,
    firmware: bytes,
    *,
    progress_cb: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    mid = int(module_id)
    if not (1 <= mid <= 254):
        return {"ok": False, "error": "invalid module_id"}
    if not firmware:
        return {"ok": False, "error": "empty firmware"}

    def _progress(pct: int, msg: str) -> None:
        if progress_cb:
            progress_cb(pct, msg)
        _LOGGER.info("[CAN OTA M%d] %s (%d%%)", mid, msg, pct)

    async def _send_config(cmd: int, args: list[int], timeout: float = 1.0) -> dict[str, Any] | None:
        wire = [mid, cmd, *args]
        while len(wire) < 8:
            wire.append(0)
        await send_can(can_v2_config_request_id(mid), wire[:8], False, False)
        return await wait_config_response(hass, mid, cmd, timeout_s=timeout)

    async def _poll_ota(expected: int, timeout_s: float) -> tuple[int, int] | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            evt = await wait_ota_status(
                hass,
                mid,
                expected=expected,
                timeout_s=min(0.05, remaining) or 0.05,
            )
            if evt is None:
                continue
            status = int(evt.get("status_code", -1))
            ack_seq = int(evt.get("seq", 0))
            if status == expected:
                return status, ack_seq
            if status in (OTA_STATUS_ERROR, OTA_STATUS_NACK):
                return status, ack_seq
        return None

    try:
        _progress(2, "OTA_ABORT")
        await _send_config(COMMAND_OTA_ABORT, [], timeout=1.0)

        now_epoch = int(time.time())
        await _send_config(
            COMMAND_OTA_SET_TIMESTAMP,
            list(now_epoch.to_bytes(4, "little")),
            timeout=1.0,
        )

        size = len(firmware)
        _progress(5, "OTA_BEGIN")
        begin_resp = await _send_config(
            COMMAND_OTA_BEGIN,
            list(size.to_bytes(4, "little")),
            timeout=3.0,
        )
        if begin_resp is None or int(begin_resp.get("status_code", 255)) != 0:
            return {"ok": False, "error": "OTA_BEGIN rejected"}

        await _poll_ota(OTA_STATUS_READY, 0.8)

        payload_len = OTA_PAYLOAD_BYTES
        total_frames = (size + payload_len - 1) // payload_len
        seq = 0
        retries = 0
        max_retries = 8
        frame_interval = 0.004

        async def _send_frame(frame_seq: int) -> None:
            offset = frame_seq * payload_len
            chunk = firmware[offset : offset + payload_len]
            data = [0] * 8
            data[0] = frame_seq & 0xFF
            data[1] = (frame_seq >> 8) & 0xFF
            data[2] = (frame_seq >> 16) & 0xFF
            for i, b_val in enumerate(chunk):
                data[3 + i] = int(b_val) & 0xFF
            await send_can(can_v2_ota_data_id(mid), data, False, False)

        while seq < total_frames:
            batch_start = seq
            batch_count = min(OTA_BATCH_FRAMES, total_frames - seq)
            for _ in range(batch_count):
                await _send_frame(seq)
                seq += 1
                if frame_interval > 0:
                    await asyncio.sleep(frame_interval)
                if seq % 64 == 0:
                    pct = int((seq / total_frames) * 85) + 10
                    _progress(pct, f"Transfer {seq}/{total_frames}")

            result = await _poll_ota(OTA_STATUS_READY, 4.0)
            if result is None:
                retries += 1
                if retries > max_retries:
                    return {"ok": False, "error": "OTA ACK timeout", "seq": seq}
                seq = batch_start
                continue
            status, ack_seq = result
            if status == OTA_STATUS_ERROR:
                return {"ok": False, "error": "module OTA ERROR", "seq": ack_seq}
            if status == OTA_STATUS_NACK:
                retries += 1
                if retries > max_retries:
                    return {"ok": False, "error": "too many NACK", "seq": ack_seq}
                seq = ack_seq
                continue
            retries = 0

        _progress(95, "OTA_END")
        end_resp = await _send_config(COMMAND_OTA_END, list(size.to_bytes(4, "little")), timeout=5.0)
        if end_resp is None or int(end_resp.get("status_code", 255)) != 0:
            return {"ok": False, "error": "OTA_END failed"}

        done = await _poll_ota(OTA_STATUS_DONE, 30.0)
        if done is None:
            return {"ok": False, "error": "OTA DONE timeout"}
        _progress(100, "OTA complete")
        return {"ok": True, "module_id": mid, "bytes": size, "frames": total_frames}
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("CAN OTA failed for module %d", mid)
        return {"ok": False, "error": str(err)}
