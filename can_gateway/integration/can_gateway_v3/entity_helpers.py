from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_ADDON_CLIENT, DOMAIN
from .types import CanFrameSender
from .coordinator import CanGatewayCoordinator

if TYPE_CHECKING:
    from .addon_client import CanGatewayAddonClient


def get_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> CanGatewayCoordinator:
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


def get_can_sender(hass: HomeAssistant, entry: ConfigEntry) -> CanFrameSender:
    return hass.data[DOMAIN][entry.entry_id]["can_send"]


def get_addon_client(hass: HomeAssistant, entry: ConfigEntry) -> CanGatewayAddonClient | None:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(runtime, dict):
        return None
    client = runtime.get(DATA_ADDON_CLIENT)
    return client if client is not None else None
