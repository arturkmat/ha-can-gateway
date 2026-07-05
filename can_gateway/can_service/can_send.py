from __future__ import annotations

from protocol_constants import (
    CAN_V2_CLASS_CONFIG_REQUEST,
    can_v2_frame_class,
    is_plaintext_telemetry_id,
)
from can_secure_transport import SecureCanTransport, is_plaintext_bootstrap_tx


def prepare_outgoing_frames(
    transport: SecureCanTransport | None,
    module_id: int,
    can_id: int,
    data: list[int],
    *,
    module_has_master_key: bool | None = None,
) -> list[tuple[int, list[int]]] | None:
    raw = bytes(int(b) & 0xFF for b in data)
    padded = list(raw) + [0] * (8 - len(raw))

    if is_plaintext_bootstrap_tx(can_id, raw):
        return [(can_id, padded)]

    if transport is None:
        if is_plaintext_telemetry_id(can_id):
            return [(can_id, padded)]
        return None

    if is_plaintext_telemetry_id(can_id) and module_has_master_key is False:
        return [(can_id, padded)]

    if can_v2_frame_class(can_id) == CAN_V2_CLASS_CONFIG_REQUEST:
        frames = transport.build_secure_config_request(module_id, data)
    else:
        frames = transport.wrap_outgoing(module_id, can_id, data)
    return frames
