from __future__ import annotations

from homeassistant.components.light import (
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import color as color_util

from .coordinator import EntityDescription
from .device_helpers import module_device_info
from .entity_helpers import get_can_sender, get_coordinator
from .led_protocol import (
    LED_EFFECT_OFF,
    LED_EFFECT_SOLID,
    LED_STRIP_TYPE_CCT,
    LED_STRIP_TYPE_RGB,
    cct_warm_cool_from_kelvin,
    kelvin_from_byte,
    kelvin_to_byte,
    pack_set_led_effect_args,
)
from .protocol import can_v2_config_request_id


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = get_coordinator(hass, entry)
    can_send = get_can_sender(hass, entry)
    entities: dict[str, CanGatewayLight] = {}

    def _add(descriptions: list[EntityDescription]) -> None:
        new_entities: list[CanGatewayLight] = []
        for desc in descriptions:
            if desc.unique_id in entities:
                continue
            ent = CanGatewayLight(coordinator, can_send, desc)
            entities[desc.unique_id] = ent
            new_entities.append(ent)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.register_platform_adder("light", _add))


class CanGatewayLight(LightEntity):
    _attr_has_entity_name = True
    _attr_supported_features = LightEntityFeature.EFFECT

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
        state = self._coordinator.get_state(self._attr_unique_id)
        attrs = {} if state is None else dict(state.attributes)
        strip_type = int(attrs.get("strip_type", LED_STRIP_TYPE_RGB))
        if strip_type == LED_STRIP_TYPE_CCT:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
        else:
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
        self.async_write_ha_state()

    def _light_state(self) -> dict:
        state = self._coordinator.get_state(self._attr_unique_id)
        if state is None or not isinstance(state.value, dict):
            return {}
        return state.value

    @property
    def is_on(self) -> bool | None:
        val = self._light_state()
        if not val:
            return None
        return bool(val.get("is_on", False))

    @property
    def brightness(self) -> int | None:
        val = self._light_state()
        raw = val.get("brightness")
        if raw is None:
            return None
        return int(int(raw) * 255 / 255)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        val = self._light_state()
        if not val:
            return None
        return (int(val.get("r", 0)), int(val.get("g", 0)), int(val.get("b", 0)))

    @property
    def color_temp_kelvin(self) -> int | None:
        val = self._light_state()
        k = val.get("kelvin")
        if k is not None:
            return int(k)
        return None

    @property
    def effect_list(self) -> list[str] | None:
        return ["off", "solid", "rainbow", "chase", "breathe"]

    @property
    def effect(self) -> str | None:
        effect_map = {0: "off", 1: "solid", 2: "rainbow", 3: "chase", 4: "breathe", 5: "identify"}
        val = self._light_state()
        return effect_map.get(int(val.get("effect", 0)), "off")

    @property
    def extra_state_attributes(self):
        state = self._coordinator.get_state(self._attr_unique_id)
        return {} if state is None else dict(state.attributes)

    @property
    def device_info(self):
        return module_device_info(self._coordinator, self._desc.module_id)

    def _strip_meta(self) -> tuple[int, int]:
        state = self._coordinator.get_state(self._attr_unique_id)
        attrs = {} if state is None else dict(state.attributes)
        return int(attrs.get("strip_index", 1)), int(attrs.get("strip_type", LED_STRIP_TYPE_RGB))

    async def _send_effect(
        self,
        effect_id: int,
        duration_s: int,
        r: int,
        g: int,
        b: int,
    ) -> None:
        strip_index, strip_type = self._strip_meta()
        args = pack_set_led_effect_args(
            effect_id, duration_s, r, g, b, strip_index=strip_index, strip_type=strip_type
        )
        wire = [self._desc.module_id, 111, *args, 0, 0, 0]
        await self._can_send(can_v2_config_request_id(self._desc.module_id), wire[:8], False, False)
        val = self._light_state()
        val.update({"is_on": effect_id != LED_EFFECT_OFF, "effect": effect_id, "r": r, "g": g, "b": b})
        self._coordinator._set_state(self._attr_unique_id, val, self.extra_state_attributes)

    async def async_turn_on(self, **kwargs) -> None:
        strip_index, strip_type = self._strip_meta()
        effect_id = LED_EFFECT_SOLID
        duration_s = 0
        r, g, b = 255, 255, 255
        if strip_type == LED_STRIP_TYPE_CCT:
            kelvin = kwargs.get("color_temp_kelvin") or kwargs.get("kelvin") or 4000
            r, g = cct_warm_cool_from_kelvin(int(kelvin))
            b = 0
        elif "rgb_color" in kwargs:
            r, g, b = kwargs["rgb_color"]
        elif "color_temp_kelvin" in kwargs:
            hs = color_util.color_temperature_to_hs(kwargs["color_temp_kelvin"])
            r, g, b = color_util.color_hs_to_RGB(*hs)
        if "brightness" in kwargs and kwargs["brightness"] is not None:
            scale = int(kwargs["brightness"]) / 255.0
            r, g, b = (int(c * scale) for c in (r, g, b))
        if kwargs.get("effect") == "off":
            effect_id = LED_EFFECT_OFF
        await self._send_effect(effect_id, duration_s, r, g, b)

    async def async_turn_off(self, **kwargs) -> None:
        strip_index, strip_type = self._strip_meta()
        await self._send_effect(LED_EFFECT_OFF, 0, 0, 0, 0)
