from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONFIG_CMD_GET_LED_STRIP_CONFIG,
    CONFIG_CMD_GET_MCP23017_ROLE_DUMP,
    CONFIG_CMD_GET_SHUTTER_RELAYS,
    MAX_SHUTTER_SLOTS,
    DOMAIN,
    GATEWAY_DEVICE_ID,
)
from .coordinator import EntityDescription
from .device_helpers import gateway_device_info, module_device_info
from .entity_helpers import get_can_sender, get_coordinator
from .led_protocol import MAX_LED_STRIPS
from .protocol import (
    COMMAND_GET_MODULE_NAME,
    COMMAND_IDENTIFY,
    can_v2_config_request_id,
    module_name_read_offsets,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = get_coordinator(hass, entry)
    can_send = get_can_sender(hass, entry)
    async_add_entities(
        [
            GatewayFullScanButton(coordinator, can_send),
            GatewayDeepScanButton(coordinator, can_send),
            GatewayRefreshMetadataButton(coordinator, can_send),
            GatewayScanSelectedButton(coordinator, can_send),
            GatewayIdentifySelectedButton(coordinator, can_send),
            GatewayRebootSelectedButton(coordinator, can_send),
            GatewayRebootButton(coordinator, can_send),
        ]
    )

    # V5.0.13 FIX: Dynamic catalog buttons from add-on (pulse relays) were never
    # created — this platform only exported static Gateway buttons. Add-on exports
    # entities with platform="button" for relays configured with pulse_ms > 0
    # (unique_id "..._pulse"); nothing ever registered a platform adder for them.
    dynamic_entities: dict[str, CanGatewayPulseButton] = {}

    def _add(descriptions: list[EntityDescription]) -> None:
        new_entities: list[CanGatewayPulseButton] = []
        for desc in descriptions:
            if desc.unique_id in dynamic_entities:
                continue
            ent = CanGatewayPulseButton(coordinator, can_send, desc)
            dynamic_entities[desc.unique_id] = ent
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.register_platform_adder("button", _add))


class CanGatewayPulseButton(ButtonEntity):
    """Dynamic pulse-relay button sourced from the add-on entity catalog."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, can_send, desc: EntityDescription) -> None:
        self._coordinator = coordinator
        self._can_send = can_send
        self._desc = desc
        self._attr_unique_id = desc.unique_id
        self._attr_name = desc.name
        self._attr_icon = desc.icon

    async def async_press(self) -> None:
        state = self._coordinator.get_state(self._attr_unique_id)
        relay_no = None if state is None else state.attributes.get("relay_no")
        if relay_no is None:
            return
        await self._can_send(
            can_v2_config_request_id(self._desc.module_id),
            [self._desc.module_id, 59, int(relay_no), 1, 0, 0, 0, 0],
            False,
            False,
        )
        if self._desc.module_id == 103 and int(relay_no) == 23:
            self._coordinator.pulse_binary_sensor(
                "m201_gpio120_binary",
                {
                    "module_id": 201,
                    "gpio": 120,
                    "mapped_from": self._attr_unique_id,
                },
            )

    @property
    def extra_state_attributes(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        return {} if state is None else dict(state.attributes)

    @property
    def device_info(self) -> DeviceInfo:
        return module_device_info(self._coordinator, self._desc.module_id)


class GatewayBaseButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, can_send) -> None:
        self._coordinator = coordinator
        self._can_send = can_send

    @property
    def device_info(self) -> DeviceInfo:
        return gateway_device_info()

    @property
    def extra_state_attributes(self):
        return {"gpio_no": None}


class GatewayFullScanButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_reinitialize_scan"
    _attr_name = "Gateway Reinitialize Scan"
    _attr_icon = "mdi:reload"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator, can_send)

    async def async_press(self) -> None:
        commands = (
            [255, 3, 0, 0, 0, 0, 0, 0],   # GET_SUMMARY
            [255, 40, 0, 0, 0, 0, 0, 0],  # SCAN_SENSORS
            [255, 67, 0, 0, 0, 0, 0, 0],  # SCAN_MCP23017
        )
        for data in commands:
            await self._can_send(can_v2_config_request_id(0xFF), data, False, False)
            await asyncio.sleep(0.2)


class GatewayRebootButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_reboot"
    _attr_name = "Gateway Reboot"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator, can_send)

    async def async_press(self) -> None:
        await self._can_send(can_v2_config_request_id(0xFF), [255, 58, 0, 0, 0, 0, 0, 0], False, False)


class GatewayIdentifySelectedButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_identify_selected"
    _attr_name = "Gateway Identify Selected Module"
    _attr_icon = "mdi:crosshairs-question"

    async def async_press(self) -> None:
        module_id = self._coordinator.selected_module_id
        if module_id is None:
            known = self._coordinator.get_known_module_ids()
            if not known:
                return
            module_id = known[0]
        await self._can_send(
            can_v2_config_request_id(int(module_id)),
            [int(module_id), COMMAND_IDENTIFY, 5, 0, 0, 0, 0, 0],
            False,
            False,
        )


class GatewayRebootSelectedButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_reboot_selected"
    _attr_name = "Gateway Reboot Selected Module"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator, can_send)

    async def async_press(self) -> None:
        module_id = self._coordinator.selected_module_id
        if module_id is None:
            known = self._coordinator.get_known_module_ids()
            if not known:
                return
            module_id = known[0]
        await self._can_send(
            can_v2_config_request_id(int(module_id)),
            [int(module_id), 58, 0, 0, 0, 0, 0, 0],
            False,
            False,
        )


class GatewayDeepScanButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_deep_scan"
    _attr_name = "Gateway Deep Scan"
    _attr_icon = "mdi:radar"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator, can_send)

    async def async_press(self) -> None:
        # More aggressive scan pass with repeated broadcasts.
        for _ in range(2):
            for data in (
                [255, 3, 0, 0, 0, 0, 0, 0],
                [255, 3, 0, 0, 0, 0, 0, 0],
                [255, 40, 0, 0, 0, 0, 0, 0],
                [255, 67, 0, 0, 0, 0, 0, 0],
            ):
                await self._can_send(can_v2_config_request_id(0xFF), data, False, False)
                await asyncio.sleep(0.15)
            await asyncio.sleep(0.8)


class GatewayRefreshMetadataButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_refresh_metadata"
    _attr_name = "Gateway Refresh Module Metadata"
    _attr_icon = "mdi:file-refresh"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator, can_send)

    async def async_press(self) -> None:
        for module_id in self._coordinator.get_known_module_ids():
            for cmd in (3, 24, 40, 67):
                await self._can_send(
                    can_v2_config_request_id(int(module_id)),
                    [int(module_id), cmd, 0, 0, 0, 0, 0, 0],
                    False,
                    False,
                )
                await asyncio.sleep(0.1)
            for offset in module_name_read_offsets():
                await self._can_send(
                    can_v2_config_request_id(int(module_id)),
                    [int(module_id), COMMAND_GET_MODULE_NAME, offset, 0, 0, 0, 0, 0],
                    False,
                    False,
                )
                await asyncio.sleep(0.055)
            for chip in range(8):
                await self._can_send(
                    can_v2_config_request_id(int(module_id)),
                    [
                        int(module_id),
                        CONFIG_CMD_GET_MCP23017_ROLE_DUMP,
                        chip,
                        0,
                        0,
                        0,
                        0,
                        0,
                    ],
                    False,
                    False,
                )
                await asyncio.sleep(0.055)
            for strip_idx in range(1, MAX_LED_STRIPS + 1):
                self._coordinator.note_led_strip_query(int(module_id), strip_idx)
                await self._can_send(
                    can_v2_config_request_id(int(module_id)),
                    [
                        int(module_id),
                        CONFIG_CMD_GET_LED_STRIP_CONFIG,
                        strip_idx,
                        0,
                        0,
                        0,
                        0,
                        0,
                    ],
                    False,
                    False,
                )
                await asyncio.sleep(0.06)
        await asyncio.sleep(0.35)
        for module_id in self._coordinator.get_known_module_ids():
            for shutter_no in range(1, MAX_SHUTTER_SLOTS + 1):
                await self._can_send(
                    can_v2_config_request_id(int(module_id)),
                    [
                        int(module_id),
                        CONFIG_CMD_GET_SHUTTER_RELAYS,
                        int(shutter_no),
                        0,
                        0,
                        0,
                        0,
                        0,
                    ],
                    False,
                    False,
                )
                await asyncio.sleep(0.08)


class GatewayScanSelectedButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_scan_selected"
    _attr_name = "Gateway Scan Selected Module"
    _attr_icon = "mdi:target-account"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator, can_send)

    async def async_press(self) -> None:
        module_id = self._coordinator.selected_module_id
        if module_id is None:
            known = self._coordinator.get_known_module_ids()
            if not known:
                return
            module_id = known[0]
        for cmd in (3, 24, 40, 67):
            await self._can_send(
                can_v2_config_request_id(int(module_id)),
                [int(module_id), cmd, 0, 0, 0, 0, 0, 0],
                False,
                False,
            )
            await asyncio.sleep(0.12)
        for offset in module_name_read_offsets():
            await self._can_send(
                can_v2_config_request_id(int(module_id)),
                [int(module_id), COMMAND_GET_MODULE_NAME, offset, 0, 0, 0, 0, 0],
                False,
                False,
            )
            await asyncio.sleep(0.055)
        for chip in range(8):
            await self._can_send(
                can_v2_config_request_id(int(module_id)),
                [
                    int(module_id),
                    CONFIG_CMD_GET_MCP23017_ROLE_DUMP,
                    chip,
                    0,
                    0,
                    0,
                    0,
                    0,
                ],
                False,
                False,
            )
            await asyncio.sleep(0.055)
        await asyncio.sleep(0.2)
        for shutter_no in range(1, MAX_SHUTTER_SLOTS + 1):
            await self._can_send(
                can_v2_config_request_id(int(module_id)),
                [
                    int(module_id),
                    CONFIG_CMD_GET_SHUTTER_RELAYS,
                    int(shutter_no),
                    0,
                    0,
                    0,
                    0,
                    0,
                ],
                False,
                False,
            )
            await asyncio.sleep(0.08)
