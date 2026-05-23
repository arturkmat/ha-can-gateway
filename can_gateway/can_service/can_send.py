from __future__ import annotations

from protocol_constants import (
    CAN_ID_CONFIG_REQUEST,
    CAN_ID_SECURE_TLV_REQUEST,
    CAN_ID_SECURE_TLV_RESPONSE,
    PLAINTEXT_TELEMETRY_CAN_IDS,
)
from can_secure_transport import SecureCanTransport, is_plaintext_bootstrap_tx

_PREWRAPPED_CAN_IDS = frozenset({CAN_ID_SECURE_TLV_REQUEST, CAN_ID_SECURE_TLV_RESPONSE})


def prepare_outgoing_frames(
    transport: SecureCanTransport | None,
    module_id: int,
    can_id: int,
    data: list[int],
) -> list[tuple[int, list[int]]] | None:
    raw = bytes(int(b) & 0xFF for b in data)
    padded = list(raw) + [0] * (8 - len(raw))

    # Już zsegmentowane ramki Secure TLV — nie owijaj ponownie (regresja wydajności HA).
    if can_id in _PREWRAPPED_CAN_IDS:
        return [(can_id, padded)]

    if is_plaintext_bootstrap_tx(can_id, raw) or can_id in PLAINTEXT_TELEMETRY_CAN_IDS:
        return [(can_id, padded)]

    if transport is None:
        return None

    if can_id == CAN_ID_CONFIG_REQUEST:
        frames = transport.build_secure_config_request(module_id, data)
    else:
        frames = transport.wrap_outgoing(module_id, can_id, data)
    return frames
