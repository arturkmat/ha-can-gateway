"""OTA upload firmware over CAN — adaptacja run_headless_ota z konfiguratora."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from protocol_constants import (
    CAN_V2_CLASS_OTA_STATUS,
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
    can_v2_frame_class,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager

_LOGGER = logging.getLogger(__name__)


def _poll_ota_status(
    bus: BusManager,
    module_id: int,
    *,
    expected: int,
    timeout_s: float,
) -> tuple[int, int] | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        bus.pump_rx(min(0.05, max(0.0, deadline - time.time())))
        msg = bus._recv(0.02)  # noqa: SLF001
        if msg is None:
            continue
        aid = int(getattr(msg, "arbitration_id", 0))
        if can_v2_frame_class(aid) != CAN_V2_CLASS_OTA_STATUS:
            continue
        data = list(getattr(msg, "data", b""))
        if len(data) < 2 or int(data[0]) != int(module_id):
            continue
        status = int(data[1])
        ack_seq = 0
        if len(data) >= 5:
            ack_seq = int(data[2]) | (int(data[3]) << 8) | (int(data[4]) << 16)
        if status == expected:
            return status, ack_seq
        if status in (OTA_STATUS_ERROR, OTA_STATUS_NACK):
            return status, ack_seq
    return None


def upload_firmware(
    bus: BusManager,
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
        _LOGGER.info("[OTA] %s (%d%%)", msg, pct)

    if not bus._begin_exclusive_io():  # noqa: SLF001
        return {"ok": False, "error": "bus busy"}

    try:
        _progress(2, "OTA_ABORT")
        bus.send_config_and_wait(mid, COMMAND_OTA_ABORT, timeout=1.0)

        now_epoch = int(time.time())
        bus.send_config_and_wait(
            mid,
            COMMAND_OTA_SET_TIMESTAMP,
            list(now_epoch.to_bytes(4, "little")),
            timeout=1.0,
        )

        size = len(firmware)
        _progress(5, "OTA_BEGIN")
        resp = bus.send_config_and_wait(
            mid,
            COMMAND_OTA_BEGIN,
            list(size.to_bytes(4, "little")),
            timeout=3.0,
        )
        if resp is None or len(resp) < 3 or int(resp[2]) != 0:
            return {"ok": False, "error": "OTA_BEGIN rejected"}

        _poll_ota_status(bus, mid, expected=OTA_STATUS_READY, timeout_s=0.8)

        payload_len = OTA_PAYLOAD_BYTES
        total_frames = (size + payload_len - 1) // payload_len
        seq = 0
        retries = 0
        max_retries = 8
        frame_interval = 0.004

        def _send_frame(frame_seq: int) -> None:
            offset = frame_seq * payload_len
            chunk = firmware[offset : offset + payload_len]
            data = [0] * 8
            data[0] = frame_seq & 0xFF
            data[1] = (frame_seq >> 8) & 0xFF
            data[2] = (frame_seq >> 16) & 0xFF
            for i, b in enumerate(chunk):
                data[3 + i] = int(b) & 0xFF
            bus.send_ota_data(mid, data)

        while seq < total_frames:
            batch_start = seq
            batch_count = min(OTA_BATCH_FRAMES, total_frames - seq)
            for _ in range(batch_count):
                _send_frame(seq)
                seq += 1
                if frame_interval > 0:
                    time.sleep(frame_interval)
                if seq % 64 == 0:
                    pct = int((seq / total_frames) * 85) + 10
                    _progress(pct, f"Transfer {seq}/{total_frames}")

            result = _poll_ota_status(bus, mid, expected=OTA_STATUS_READY, timeout_s=4.0)
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
        end_resp = bus.send_config_and_wait(mid, COMMAND_OTA_END, timeout=5.0)
        if end_resp is None or len(end_resp) < 3 or int(end_resp[2]) != 0:
            return {"ok": False, "error": "OTA_END failed"}

        done = _poll_ota_status(bus, mid, expected=OTA_STATUS_DONE, timeout_s=30.0)
        if done is None:
            return {"ok": False, "error": "OTA DONE timeout"}
        _progress(100, "OTA complete")
        return {"ok": True, "module_id": mid, "bytes": size, "frames": total_frames}
    finally:
        bus._end_exclusive_io()  # noqa: SLF001
