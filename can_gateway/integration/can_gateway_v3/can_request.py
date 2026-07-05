"""Async wait helpers for CONFIG responses and OTA status on the HA event bus."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import EVENT_CONFIG_RESPONSE, EVENT_OTA_STATUS
from .protocol import CAN_V2_CLASS_OTA_STATUS, can_v2_frame_class


async def wait_config_response(
    hass: HomeAssistant,
    module_id: int,
    command: int,
    *,
    timeout_s: float = 1.0,
) -> dict[str, Any] | None:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()

    @callback
    def _handler(event) -> None:
        data = event.data
        if (
            int(data.get("module_id", -1)) == int(module_id)
            and int(data.get("command", -1)) == int(command)
            and not future.done()
        ):
            future.set_result(data)

    remove = hass.bus.async_listen(EVENT_CONFIG_RESPONSE, _handler)
    try:
        return await asyncio.wait_for(future, timeout=timeout_s)
    except TimeoutError:
        return None
    finally:
        remove()


async def wait_ota_status(
    hass: HomeAssistant,
    module_id: int,
    *,
    expected: int | None = None,
    timeout_s: float = 1.0,
) -> dict[str, Any] | None:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()

    @callback
    def _handler(event) -> None:
        data = event.data
        can_id = int(data.get("can_id", 0))
        if can_v2_frame_class(can_id) != CAN_V2_CLASS_OTA_STATUS:
            return
        raw = data.get("data") or []
        if raw and int(raw[0]) != int(module_id):
            return
        status_code = int(data.get("status_code", -1))
        if expected is not None:
            if status_code == int(expected):
                pass
            elif status_code in (2, 3, 4):  # NACK, DONE, ERROR
                pass
            else:
                return
        if not future.done():
            future.set_result(data)

    remove = hass.bus.async_listen(EVENT_OTA_STATUS, _handler)
    try:
        return await asyncio.wait_for(future, timeout=timeout_s)
    except TimeoutError:
        return None
    finally:
        remove()
