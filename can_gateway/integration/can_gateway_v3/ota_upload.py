"""CAN OTA firmware upload via add-on REST (no HA event-bus wait)."""

from __future__ import annotations

import logging
from typing import Any

from .addon_client import CanGatewayAddonClient

_LOGGER = logging.getLogger(__name__)


async def upload_firmware_via_addon(
    client: CanGatewayAddonClient,
    module_id: int,
    firmware: bytes,
) -> dict[str, Any]:
    """Delegate OTA to add-on POST /api/modules/{id}/ota/upload."""
    mid = int(module_id)
    if not (1 <= mid <= 254):
        return {"ok": False, "error": "invalid module_id"}
    if not firmware:
        return {"ok": False, "error": "empty firmware"}
    _LOGGER.info("[CAN OTA M%d] uploading %d bytes via add-on REST", mid, len(firmware))
    try:
        result = await client.upload_ota(mid, firmware)
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("CAN OTA failed for module %d", mid)
        return {"ok": False, "error": str(err)}
    if not isinstance(result, dict):
        return {"ok": False, "error": "unexpected add-on response"}
    if not result.get("ok"):
        _LOGGER.error("CAN OTA failed for module %d: %s", mid, result.get("error", result))
    return result
