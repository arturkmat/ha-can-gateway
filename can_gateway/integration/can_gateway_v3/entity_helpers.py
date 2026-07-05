from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .can_io import CanFrameSender
from .const import DOMAIN
from .coordinator import CanGatewayCoordinator


def get_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> CanGatewayCoordinator:
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


def get_can_sender(hass: HomeAssistant, entry: ConfigEntry) -> CanFrameSender:
    return hass.data[DOMAIN][entry.entry_id]["can_send"]
