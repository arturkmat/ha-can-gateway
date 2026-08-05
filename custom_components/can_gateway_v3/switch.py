from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, GATEWAY_DEVICE_ID
from .entity_helpers import get_can_sender, get_coordinator
from .protocol import can_v2_config_request_id
from .coordinator import EntityDescription
from .device_helpers import module_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = get_coordinator(hass, entry)
    can_send = get_can_sender(hass, entry)
    entities: dict[str, CanGatewaySwitch] = {}

    def _add(descriptions: list[EntityDescription]) -> None:
        new_entities: list[CanGatewaySwitch] = []
        for desc in descriptions:
            if desc.unique_id in entities:
                continue
            # Debug: log binding_type
            binding_type = desc.get("binding_type", "unknown")
            _LOGGER.debug(f"Creating switch for {desc.get('name')} (binding_type={binding_type})")
            
            ent = CanGatewaySwitch(coordinator, can_send, desc)
            entities[desc.unique_id] = ent
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    async def _prune_removed_switch_entities() -> None:
        for uid in list(entities.keys()):
            if uid in coordinator.entity_descriptions:
                continue
            ent = entities.pop(uid, None)
            if ent is not None:
                await ent.async_remove()

    def _schedule_switch_prune() -> None:
        hass.async_create_task(_prune_removed_switch_entities())

    entry.async_on_unload(coordinator.register_platform_adder("switch", _add))
    entry.async_on_unload(coordinator.register_switch_prune_listener(_schedule_switch_prune))


class CanGatewaySwitch(SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, can_send, desc: EntityDescription) -> None:
        self._coordinator = coordinator
        self._can_send = can_send
        self._desc = desc
        self._attr_unique_id = desc.unique_id
        self._attr_name = desc.name
        self._attr_icon = desc.icon

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.register_state_listener(self._on_state_change))
        self._on_state_change(self._attr_unique_id)

    def _on_state_change(self, unique_id: str) -> None:
        if unique_id != self._attr_unique_id:
            return
        self.async_write_ha_state()

    @property
    def is_on(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        return None if state is None else bool(state.value)

    @property
    def extra_state_attributes(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        attrs = {} if state is None else dict(state.attributes)
        attrs.setdefault("gpio_no", None)
        info = self._coordinator.get_module_info(self._desc.module_id)
        attrs["module_name"] = info.name or f"CAN Module {self._desc.module_id}"
        attrs["module_hw_name"] = info.hw_name or "Unknown"
        attrs["module_hw_type"] = info.hw_type
        attrs["firmware_version"] = info.fw_version
        attrs["firmware_build_datetime"] = info.firmware_build_datetime
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        state = self._coordinator.get_state(self._attr_unique_id)
        relay_no = None if state is None else state.attributes.get("relay_no")
        if relay_no is None:
            relay_no = _extract_index(self._attr_unique_id, "_relay")
        if relay_no is None:
            return
        await self._can_send(
            can_v2_config_request_id(self._desc.module_id),
            [self._desc.module_id, 59, int(relay_no), 1, 0, 0, 0, 0],
            False,
            False,
        )

    async def async_turn_off(self, **kwargs) -> None:
        state = self._coordinator.get_state(self._attr_unique_id)
        relay_no = None if state is None else state.attributes.get("relay_no")
        if relay_no is None:
            relay_no = _extract_index(self._attr_unique_id, "_relay")
        if relay_no is None:
            return
        await self._can_send(
            can_v2_config_request_id(self._desc.module_id),
            [self._desc.module_id, 59, int(relay_no), 0, 0, 0, 0, 0],
            False,
            False,
        )

    @property
    def device_info(self) -> DeviceInfo:
        return module_device_info(self._coordinator, self._desc.module_id)


def _extract_index(unique_id: str, marker: str) -> int | None:
    pos = unique_id.find(marker)
    if pos < 0:
        return None
    raw = unique_id[pos + len(marker):]
    try:
        return int(raw)
    except ValueError:
        return None
