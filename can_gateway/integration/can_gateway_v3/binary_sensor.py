from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, GATEWAY_DEVICE_ID
from .entity_helpers import get_coordinator
from .coordinator import EntityDescription


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = get_coordinator(hass, entry)
    entities: dict[str, CanGatewayBinarySensor] = {}

    def _add(descriptions: list[EntityDescription]) -> None:
        new_entities: list[CanGatewayBinarySensor] = []
        for desc in descriptions:
            if desc.unique_id in entities:
                continue
            ent = CanGatewayBinarySensor(coordinator, desc)
            entities[desc.unique_id] = ent
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.register_platform_adder("binary_sensor", _add))


class CanGatewayBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, desc: EntityDescription) -> None:
        self._coordinator = coordinator
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
    def is_on(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        value = None if state is None else state.value
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "on", "yes", "high", "pressed", "active"}:
                return True
            if normalized in {"0", "false", "off", "no", "low", "released", "inactive", "unknown", "none", "null", ""}:
                return False
        return bool(value)

    @property
    def extra_state_attributes(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        attrs = {} if state is None else dict(state.attributes)
        attrs.setdefault("gpio_no", attrs.get("gpio"))
        info = self._coordinator.get_module_info(self._desc.module_id)
        attrs["module_name"] = info.name or f"CAN Module {self._desc.module_id}"
        attrs["module_hw_name"] = info.hw_name or "Unknown"
        attrs["module_hw_type"] = info.hw_type
        attrs["firmware_version"] = info.fw_version
        attrs["firmware_build_datetime"] = info.firmware_build_datetime
        return attrs

    @property
    def device_info(self) -> DeviceInfo:
        info = self._coordinator.get_module_info(self._desc.module_id)
        name = f"CAN Module {self._desc.module_id} {(info.name or '').strip()}".strip()
        model = info.hw_name or (f"HW {info.hw_type}" if info.hw_type is not None else "Unknown")
        sw = info.firmware_build_datetime or info.fw_version
        return DeviceInfo(
            identifiers={(DOMAIN, f"module_{self._desc.module_id}")},
            name=name,
            manufacturer="Dark-Smart",
            model=model,
            hw_version=model,
            sw_version=sw,
            serial_number=info.mac,
            via_device=(DOMAIN, GATEWAY_DEVICE_ID),
        )
