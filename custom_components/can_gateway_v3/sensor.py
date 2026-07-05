from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CAN_BITRATE,
    CONF_SERIAL_BAUDRATE,
    CONF_SERIAL_PORT,
    DEFAULT_CAN_BITRATE,
    DEFAULT_SERIAL_BAUDRATE,
    DEFAULT_SERIAL_PORT,
    DOMAIN,
    GATEWAY_DEVICE_ID,
)
from .entity_helpers import get_coordinator
from .coordinator import EntityDescription


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = get_coordinator(hass, entry)
    entities: dict[str, CanGatewaySensor] = {}
    async_add_entities([GatewayStatusSensor(coordinator, entry), GatewayLastScanSensor(coordinator)])

    def _add(descriptions: list[EntityDescription]) -> None:
        new_entities: list[CanGatewaySensor] = []
        for desc in descriptions:
            if desc.unique_id in entities:
                continue
            ent = CanGatewaySensor(coordinator, desc)
            entities[desc.unique_id] = ent
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.register_platform_adder("sensor", _add))


class CanGatewaySensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, desc: EntityDescription) -> None:
        self._coordinator = coordinator
        self._desc = desc
        self._attr_unique_id = desc.unique_id
        self._attr_name = desc.name
        self._attr_device_class = desc.device_class
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_icon = desc.icon

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.register_state_listener(self._on_state_change))
        self._on_state_change(self._attr_unique_id)

    def _on_state_change(self, unique_id: str) -> None:
        if unique_id != self._attr_unique_id:
            return
        self.async_write_ha_state()

    @property
    def native_value(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        return None if state is None else state.value

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


class GatewayStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_status"
    _attr_name = "Gateway Status"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._tracked_state_key = "__gateway_status__"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.register_state_listener(self._on_state_change))
        self._on_state_change(self._tracked_state_key)

    def _on_state_change(self, unique_id: str) -> None:
        # Re-render gateway status whenever any coordinator state changes.
        if unique_id != self._tracked_state_key and not unique_id.startswith("m"):
            return
        self.async_write_ha_state()

    @property
    def native_value(self):
        module_count = len(self._coordinator.module_info)
        return f"online ({module_count} modules)"

    @property
    def extra_state_attributes(self):
        module_ids = sorted(self._coordinator.module_info.keys())
        named_modules = {
            str(mid): info.name
            for mid, info in sorted(self._coordinator.module_info.items())
            if isinstance(info.name, str) and info.name
        }
        hw_modules = {
            str(mid): info.hw_name
            for mid, info in sorted(self._coordinator.module_info.items())
            if isinstance(info.hw_name, str) and info.hw_name
        }
        build_modules = {
            str(mid): info.firmware_build_datetime
            for mid, info in sorted(self._coordinator.module_info.items())
            if isinstance(info.firmware_build_datetime, str) and info.firmware_build_datetime
        }
        return {
            "serial_port": self._entry.data.get(CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT),
            "serial_baudrate": self._entry.data.get(CONF_SERIAL_BAUDRATE, DEFAULT_SERIAL_BAUDRATE),
            "can_bitrate": self._entry.data.get(CONF_CAN_BITRATE, DEFAULT_CAN_BITRATE),
            "selected_module_id": self._coordinator.selected_module_id,
            "module_count": len(module_ids),
            "module_ids": module_ids,
            "module_names": named_modules,
            "module_hw_names": hw_modules,
            "module_firmware_build": build_modules,
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, GATEWAY_DEVICE_ID)},
            name="CAN Gateway v3",
            manufacturer="Dark-Smart",
            model="USB-CAN (SLCAN)",
        )


class GatewayLastScanSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_last_scan"
    _attr_name = "Gateway Last Scan"
    _attr_icon = "mdi:radar"

    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator
        self._tracked_state_key = "__gateway_status__"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.register_state_listener(self._on_state_change))
        self._on_state_change(self._tracked_state_key)

    def _on_state_change(self, unique_id: str) -> None:
        if unique_id != self._tracked_state_key:
            return
        self.async_write_ha_state()

    @property
    def native_value(self):
        return self._coordinator.last_scan_status or "never"

    @property
    def extra_state_attributes(self):
        return {
            "started_at": self._coordinator.last_scan_started_at,
            "finished_at": self._coordinator.last_scan_finished_at,
            "stage": self._coordinator.last_scan_stage,
            "modules_found_count": len(self._coordinator.last_scan_modules),
            "modules_found_ids": self._coordinator.last_scan_modules,
            "details": self._coordinator.last_scan_details,
        }

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, GATEWAY_DEVICE_ID)},
            name="CAN Gateway v3",
            manufacturer="Dark-Smart",
            model="USB-CAN (SLCAN)",
        )


