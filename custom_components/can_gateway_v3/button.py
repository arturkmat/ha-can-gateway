from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import GATEWAY_DEVICE_ID
from .coordinator import EntityDescription
from .device_helpers import gateway_device_info, module_device_info
from .entity_helpers import (
    get_addon_client,
    get_can_sender,
    get_catalog_refresh,
    get_coordinator,
)
from .protocol import COMMAND_IDENTIFY, can_v2_config_request_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = get_coordinator(hass, entry)
    can_send = get_can_sender(hass, entry)
    client = get_addon_client(hass, entry)
    refresh_catalog = get_catalog_refresh(hass, entry)
    async_add_entities(
        [
            GatewayFullScanButton(coordinator, client, refresh_catalog),
            GatewayDeepScanButton(coordinator, client, refresh_catalog),
            GatewayRefreshMetadataButton(coordinator, client, refresh_catalog),
            GatewayScanSelectedButton(coordinator, client, refresh_catalog),
            GatewayIdentifySelectedButton(coordinator, can_send),
            GatewayRebootSelectedButton(coordinator, can_send),
            GatewayRebootButton(coordinator, can_send),
        ]
    )

    # Dynamic catalog buttons from add-on (pulse relays with pulse_ms > 0).
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

    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator

    @property
    def device_info(self) -> DeviceInfo:
        return gateway_device_info()

    @property
    def extra_state_attributes(self):
        return {"gpio_no": None}


class GatewayAddonScanButton(GatewayBaseButton):
    """Gateway buttons that drive add-on discovery/refresh, then pull the catalog."""

    def __init__(self, coordinator, client, refresh_catalog) -> None:
        super().__init__(coordinator)
        self._client = client
        self._refresh_catalog = refresh_catalog

    async def _after_addon_scan(self, result: dict | None, stage: str) -> None:
        ok = bool(result and result.get("ok"))
        if not ok:
            detail = None if result is None else result.get("error", result)
            _LOGGER.warning("Add-on %s failed: %s", stage, detail)
            self._coordinator.mark_scan_finished(
                "error",
                f"Add-on {stage} failed: {detail}",
            )
        if self._refresh_catalog is not None:
            await self._refresh_catalog()
        if ok:
            self._coordinator.mark_scan_finished(
                "ok",
                f"Add-on {stage} finished, modules={len(self._coordinator.scanned_modules)}",
            )


class GatewayFullScanButton(GatewayAddonScanButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_reinitialize_scan"
    _attr_name = "Gateway Reinitialize Scan"
    _attr_icon = "mdi:reload"

    async def async_press(self) -> None:
        if self._client is None:
            _LOGGER.error("Gateway scan: add-on client unavailable")
            return
        self._coordinator.mark_scan_started("addon_full_scan")
        try:
            result = await self._client.discovery_scan()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Add-on full scan failed", exc_info=True)
            self._coordinator.mark_scan_finished("error", str(err))
            return
        await self._after_addon_scan(result, "full_scan")


class GatewayDeepScanButton(GatewayAddonScanButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_deep_scan"
    _attr_name = "Gateway Deep Scan"
    _attr_icon = "mdi:radar"

    async def async_press(self) -> None:
        if self._client is None:
            _LOGGER.error("Gateway deep scan: add-on client unavailable")
            return
        self._coordinator.mark_scan_started("addon_deep_scan")
        try:
            # Add-on discovery_scan already deep-reads each module (shutter_map etc.).
            result = await self._client.discovery_scan()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Add-on deep scan failed", exc_info=True)
            self._coordinator.mark_scan_finished("error", str(err))
            return
        await self._after_addon_scan(result, "deep_scan")


class GatewayRefreshMetadataButton(GatewayAddonScanButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_refresh_metadata"
    _attr_name = "Gateway Refresh Module Metadata"
    _attr_icon = "mdi:file-refresh"

    async def async_press(self) -> None:
        if self._client is None:
            _LOGGER.error("Gateway refresh metadata: add-on client unavailable")
            return
        module_ids = self._coordinator.get_known_module_ids()
        if not module_ids:
            _LOGGER.info("Gateway refresh metadata: no known modules")
            return
        self._coordinator.mark_scan_started("addon_refresh_metadata")
        last_result: dict | None = {"ok": True}
        try:
            for module_id in module_ids:
                last_result = await self._client.refresh_module(int(module_id))
                if not last_result.get("ok"):
                    _LOGGER.warning(
                        "Add-on refresh_module(%s) failed: %s",
                        module_id,
                        last_result.get("error", last_result),
                    )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Add-on refresh metadata failed", exc_info=True)
            self._coordinator.mark_scan_finished("error", str(err))
            return
        await self._after_addon_scan(last_result, "refresh_metadata")


class GatewayScanSelectedButton(GatewayAddonScanButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_scan_selected"
    _attr_name = "Gateway Scan Selected Module"
    _attr_icon = "mdi:target-account"

    async def async_press(self) -> None:
        if self._client is None:
            _LOGGER.error("Gateway scan selected: add-on client unavailable")
            return
        module_id = self._coordinator.selected_module_id
        if module_id is None:
            known = self._coordinator.get_known_module_ids()
            if not known:
                return
            module_id = known[0]
        self._coordinator.mark_scan_started("addon_refresh_selected")
        try:
            result = await self._client.refresh_module(int(module_id))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Add-on refresh selected failed", exc_info=True)
            self._coordinator.mark_scan_finished("error", str(err))
            return
        await self._after_addon_scan(result, f"refresh_module_{int(module_id)}")


class GatewayRebootButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_reboot"
    _attr_name = "Gateway Reboot"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator)
        self._can_send = can_send

    async def async_press(self) -> None:
        await self._can_send(can_v2_config_request_id(0xFF), [255, 58, 0, 0, 0, 0, 0, 0], False, False)


class GatewayIdentifySelectedButton(GatewayBaseButton):
    _attr_unique_id = f"{GATEWAY_DEVICE_ID}_identify_selected"
    _attr_name = "Gateway Identify Selected Module"
    _attr_icon = "mdi:crosshairs-question"

    def __init__(self, coordinator, can_send) -> None:
        super().__init__(coordinator)
        self._can_send = can_send

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
        super().__init__(coordinator)
        self._can_send = can_send

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
