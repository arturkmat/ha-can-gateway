from __future__ import annotations

from homeassistant.components.cover import CoverEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, GATEWAY_DEVICE_ID
from .entity_helpers import get_can_sender, get_coordinator
from .protocol import V2_CTRL_SHUTTER_CMD, can_v2_control_command_id
from .coordinator import EntityDescription
from .device_helpers import module_device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = get_coordinator(hass, entry)
    can_send = get_can_sender(hass, entry)
    entities: dict[str, CanGatewayCover] = {}

    def _add(descriptions: list[EntityDescription]) -> None:
        new_entities: list[CanGatewayCover] = []
        for desc in descriptions:
            if desc.unique_id in entities:
                continue
            ent = CanGatewayCover(coordinator, can_send, desc)
            entities[desc.unique_id] = ent
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.register_platform_adder("cover", _add))


class CanGatewayCover(CoverEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, can_send, desc: EntityDescription) -> None:
        self._coordinator = coordinator
        self._can_send = can_send
        self._desc = desc
        self._attr_unique_id = desc.unique_id
        self._attr_name = desc.name
        self._attr_device_class = desc.device_class
        self._attr_icon = desc.icon

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.register_state_listener(self._on_state_change))
        self._on_state_change(self._attr_unique_id)

    def _on_state_change(self, unique_id: str) -> None:
        if unique_id != self._attr_unique_id:
            return
        self.async_write_ha_state()

    @property
    def current_cover_position(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        if not state or not isinstance(state.value, dict):
            return None
        return state.value.get("position")

    @property
    def is_opening(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        if not state or not isinstance(state.value, dict):
            return None
        return state.value.get("direction") == 1

    @property
    def is_closing(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        if not state or not isinstance(state.value, dict):
            return None
        return state.value.get("direction") == 2

    @property
    def is_closed(self):
        pos = self.current_cover_position
        if pos is None:
            return None
        return int(pos) <= 0

    @property
    def extra_state_attributes(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        attrs = {} if state is None else dict(state.attributes)
        attrs.setdefault("gpio_no", None)
        attrs.setdefault("gpio_open_no", None)
        attrs.setdefault("gpio_close_no", None)
        info = self._coordinator.get_module_info(self._desc.module_id)
        attrs["module_name"] = info.name or f"CAN Module {self._desc.module_id}"
        attrs["module_hw_name"] = info.hw_name or "Unknown"
        attrs["module_hw_type"] = info.hw_type
        attrs["firmware_version"] = info.fw_version
        attrs["firmware_build_datetime"] = info.firmware_build_datetime
        return attrs

    async def async_open_cover(self, **kwargs) -> None:
        await self._send_shutter_command(1, 0)

    async def async_close_cover(self, **kwargs) -> None:
        await self._send_shutter_command(2, 0)

    async def async_stop_cover(self, **kwargs) -> None:
        await self._send_shutter_command(3, 0)

    async def async_set_cover_position(self, **kwargs) -> None:
        position = kwargs.get("position")
        if position is None:
            return
        target = max(0, min(100, int(position)))
        await self._send_shutter_command(4, target)

    @property
    def device_info(self) -> DeviceInfo:
        return module_device_info(self._coordinator, self._desc.module_id)

    async def _send_shutter_command(self, command: int, param: int) -> None:
        shutter_no = _extract_index(self._attr_unique_id, "_shutter")
        if shutter_no is None:
            return
        await self._can_send(
            can_v2_control_command_id(self._desc.module_id),
            [V2_CTRL_SHUTTER_CMD, shutter_no, command, param, 0, 0, 0, 0],
            False,
            False,
        )


def _extract_index(unique_id: str, marker: str) -> int | None:
    pos = unique_id.find(marker)
    if pos < 0:
        return None
    raw = unique_id[pos + len(marker):]
    try:
        return int(raw)
    except ValueError:
        return None
