from __future__ import annotations

import logging

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .addon_sync import apply_addon_entity_values
from .coordinator import EntityDescription
from .device_helpers import module_device_info
from .entity_helpers import get_addon_client, get_can_sender, get_coordinator
from .protocol import (
    SHUTTER_CMD_CLOSE,
    SHUTTER_CMD_OPEN,
    SHUTTER_CMD_SET_POSITION,
    SHUTTER_CMD_STOP,
    build_shutter_control_payload,
    can_v2_control_command_id,
)

_LOGGER = logging.getLogger(__name__)

_SHUTTER_COMMAND_NAMES = {
    SHUTTER_CMD_OPEN: "open",
    SHUTTER_CMD_CLOSE: "close",
    SHUTTER_CMD_STOP: "stop",
    SHUTTER_CMD_SET_POSITION: "position",
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = get_coordinator(hass, entry)
    can_send = get_can_sender(hass, entry)
    entities: dict[str, CanGatewayCover] = {}

    def _add(descriptions: list[EntityDescription]) -> None:
        new_entities: list[CanGatewayCover] = []
        for desc in descriptions:
            if desc.unique_id in entities:
                continue
            ent = CanGatewayCover(hass, entry, coordinator, can_send, desc)
            entities[desc.unique_id] = ent
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.register_platform_adder("cover", _add))


class CanGatewayCover(CoverEntity):
    _attr_has_entity_name = True
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, can_send, desc: EntityDescription) -> None:
        self.hass = hass
        self._entry = entry
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
        await self._send_shutter_command(SHUTTER_CMD_OPEN, 0)

    async def async_close_cover(self, **kwargs) -> None:
        await self._send_shutter_command(SHUTTER_CMD_CLOSE, 0)

    async def async_stop_cover(self, **kwargs) -> None:
        await self._send_shutter_command(SHUTTER_CMD_STOP, 0)

    async def async_set_cover_position(self, **kwargs) -> None:
        position = kwargs.get("position")
        if position is None:
            return
        target = max(0, min(100, int(position)))
        await self._send_shutter_command(SHUTTER_CMD_SET_POSITION, target)

    @property
    def device_info(self) -> DeviceInfo:
        return module_device_info(self._coordinator, self._desc.module_id)

    async def _send_shutter_command(self, command: int, param: int) -> None:
        shutter_no = self._resolve_shutter_no()
        if shutter_no is None:
            _LOGGER.warning("Cover %s: missing shutter_no in catalog attributes", self._attr_unique_id)
            return

        command_name = _SHUTTER_COMMAND_NAMES.get(int(command), "stop")
        client = get_addon_client(self.hass, self._entry)
        if client is not None:
            result = await client.set_shutter_command(
                self._desc.module_id,
                shutter_no,
                command_name,
                int(param),
            )
            if result.get("ok"):
                self._apply_optimistic_shutter_state(int(command), int(param))
                await self._refresh_addon_entity_state(client)
                return
            _LOGGER.warning(
                "Cover %s addon shutter command failed module=%s shutter=%s: %s",
                self._attr_unique_id,
                self._desc.module_id,
                shutter_no,
                result.get("error", result),
            )

        payload = build_shutter_control_payload(shutter_no, command, param)
        await self._can_send(
            can_v2_control_command_id(self._desc.module_id),
            payload,
            False,
            False,
        )
        self._apply_optimistic_shutter_state(int(command), int(param))

    async def _refresh_addon_entity_state(self, client) -> None:
        try:
            payload = await client.get_entities()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Cover %s: add-on entity refresh failed", self._attr_unique_id, exc_info=True)
            return
        entities = payload.get("entities") if isinstance(payload, dict) else None
        if not isinstance(entities, list):
            return
        apply_addon_entity_values(self._coordinator, entities)
        self._coordinator.notify_gateway_state()

    def _apply_optimistic_shutter_state(self, command: int, param: int) -> None:
        state = self._coordinator.get_state(self._attr_unique_id)
        current = dict(state.value) if state is not None and isinstance(state.value, dict) else {}
        attrs = dict(state.attributes) if state is not None else {}
        if command == SHUTTER_CMD_STOP:
            current["direction"] = 0
            current["direction_text"] = "stopped"
        elif command == SHUTTER_CMD_OPEN:
            current["direction"] = 1
            current["direction_text"] = "opening"
        elif command == SHUTTER_CMD_CLOSE:
            current["direction"] = 2
            current["direction_text"] = "closing"
        elif command == SHUTTER_CMD_SET_POSITION:
            current["direction"] = 1 if int(param) > int(current.get("position") or 0) else 2
            current["direction_text"] = "opening" if current["direction"] == 1 else "closing"
        if current:
            self._coordinator._set_state(self._attr_unique_id, current, attrs)

    def _resolve_shutter_no(self) -> int | None:
        state = self._coordinator.get_state(self._attr_unique_id)
        if state is not None:
            shutter_no = state.attributes.get("shutter_no")
            if shutter_no is not None:
                try:
                    return int(shutter_no)
                except (TypeError, ValueError):
                    pass
        return _extract_index(self._attr_unique_id, "_shutter")


def _extract_index(unique_id: str, marker: str) -> int | None:
    pos = unique_id.find(marker)
    if pos < 0:
        return None
    raw = unique_id[pos + len(marker):]
    try:
        return int(raw)
    except ValueError:
        return None
