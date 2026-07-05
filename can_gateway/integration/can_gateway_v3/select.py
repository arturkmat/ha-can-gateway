from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, GATEWAY_DEVICE_ID
from .device_helpers import gateway_device_info
from .entity_helpers import get_coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = get_coordinator(hass, entry)
    entity = GatewaySelectedModuleSelect(coordinator)
    async_add_entities([entity])

    def _on_state(_uid: str) -> None:
        if _uid in ("__gateway_selection__", "__gateway_status__"):
            entity.async_write_ha_state()

    coordinator.register_state_listener(_on_state)


class GatewaySelectedModuleSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_selected_module"
    _attr_name = "Gateway Selected Module"
    _attr_icon = "mdi:numeric"

    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator

    @property
    def device_info(self):
        return gateway_device_info()

    @property
    def extra_state_attributes(self):
        return {
            "gpio_no": None,
            "known_modules": self._coordinator.get_known_module_ids(),
        }

    @property
    def options(self) -> list[str]:
        return [str(mid) for mid in self._coordinator.get_known_module_ids()]

    @property
    def current_option(self) -> str | None:
        selected = self._coordinator.selected_module_id
        if selected is None:
            known = self._coordinator.get_known_module_ids()
            return str(known[0]) if known else None
        return str(selected)

    async def async_select_option(self, option: str) -> None:
        try:
            module_id = int(option)
        except ValueError:
            return
        if 1 <= module_id <= 254:
            self._coordinator.set_selected_module_id(module_id)
            self.async_write_ha_state()
