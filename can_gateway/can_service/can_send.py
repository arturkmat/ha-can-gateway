from __future__ import annotations


def prepare_outgoing_frames(
    module_id: int,
    can_id: int,
    data: list[int],
) -> list[tuple[int, list[int]]]:
    del module_id  # kept for call-site compatibility (plain CAN V3 only)
    raw = bytes(int(b) & 0xFF for b in data)
    padded = list(raw) + [0] * (8 - len(raw))
    return [(can_id, padded)]
