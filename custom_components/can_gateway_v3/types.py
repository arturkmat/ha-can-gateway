from __future__ import annotations

from collections.abc import Awaitable, Callable

# Shared type aliases used by both the add-on client transport and OTA/entity
# helpers. Previously lived in can_io.py alongside the (now removed) direct
# serial/SLCAN transport implementation.
CanFrameSender = Callable[[int, list[int], bool, bool], Awaitable[None]]
RawPayloadCallback = Callable[[str], None]
