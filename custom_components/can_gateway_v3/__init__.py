from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_MODULE_ID,
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_ADDON,
    CONFIG_CMD_SET_RELAY_BIND_ROUTE,
    CONFIG_CMD_CLEAR_RELAY_BIND_ROUTES,
    CONFIG_CMD_SET_LED_BINDING,
    CONFIG_CMD_CLEAR_LED_BINDINGS,
    CONFIG_CMD_BLE_OTA_ENABLE,
    CONFIG_CMD_SET_BLE_OTA_PIN,
    CONFIG_CMD_SET_RELAY_LINK,
    CONFIG_CMD_CLEAR_RELAY_LINKS,
    DOMAIN,
    PLATFORMS,
    SERVICE_IDENTIFY,
    SERVICE_SET_LED_EFFECT,
    SERVICE_SET_RELAY_LINK,
    SERVICE_CLEAR_RELAY_LINKS,
    SERVICE_SET_RELAY_BIND_ROUTE,
    SERVICE_CLEAR_RELAY_BIND_ROUTES,
    SERVICE_SET_LED_BINDING,
    SERVICE_CLEAR_LED_BINDINGS,
    SERVICE_START_CAN_OTA,
    SERVICE_ENABLE_BLE_OTA,
    SERVICE_SET_BLE_OTA_PIN,
    SERVICE_SET_RELAY_STATE,
    SERVICE_SHUTTER_COMMAND,
)
from .led_protocol import (
    LED_EFFECT_OFF,
    LED_EFFECT_SOLID,
    LED_STRIP_TYPE_CCT,
    LED_STRIP_TYPE_RGB,
    cct_warm_cool_from_kelvin,
    pack_set_led_binding_args,
    pack_set_led_effect_args,
)
from .ota_upload import upload_firmware_over_can
from .protocol import (
    BLE_OTA_PIN_MAX_LEN,
    BLE_OTA_PIN_MIN_LEN,
    CAN_BLE_OTA_PIN_CHUNK,
    COMMAND_IDENTIFY,
    COMMAND_SET_RELAY_STATE,
    RELAY_LINK_TRIGGER_ANY,
    RELAY_LINK_TRIGGER_MIRROR,
    pack_set_relay_bind_route_args,
    pack_set_relay_link_args,
    resolve_relay_link_target_state,
    SHUTTER_CMD_CLOSE,
    SHUTTER_CMD_OPEN,
    SHUTTER_CMD_SET_POSITION,
    SHUTTER_CMD_STOP,
    V2_CTRL_SHUTTER_CMD,
    can_v2_config_request_id,
    can_v2_control_command_id,
)

_LOGGER = logging.getLogger(__name__)

CORE_PLATFORMS = tuple(PLATFORMS)


@callback
def _register_services(hass: HomeAssistant, entry: ConfigEntry, send_can) -> None:
    async def _handle_identify(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        duration = int(call.data.get("duration_s", 5))
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, COMMAND_IDENTIFY, duration, 0, 0, 0, 0, 0],
            False,
            False,
        )

    async def _handle_set_relay(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        relay_no = int(call.data["relay_no"])
        state_map = {"off": 0, "on": 1, "toggle": 2}
        state = state_map.get(str(call.data.get("state", "toggle")).lower(), 2)
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, COMMAND_SET_RELAY_STATE, relay_no, state, 0, 0, 0, 0],
            False,
            False,
        )

    async def _handle_shutter(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        shutter_no = int(call.data["shutter_no"])
        cmd_map = {
            "open": SHUTTER_CMD_OPEN,
            "close": SHUTTER_CMD_CLOSE,
            "stop": SHUTTER_CMD_STOP,
            "set_position": SHUTTER_CMD_SET_POSITION,
        }
        command = cmd_map[str(call.data["command"]).lower()]
        param = int(call.data.get("position", 0)) if command == SHUTTER_CMD_SET_POSITION else 0
        await send_can(
            can_v2_control_command_id(module_id),
            [V2_CTRL_SHUTTER_CMD, shutter_no, command, param, 0, 0, 0, 0],
            False,
            False,
        )

    async def _handle_led_effect(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        strip_index = int(call.data.get("strip_index", 1))
        effect_id = int(call.data.get("effect_id", LED_EFFECT_SOLID))
        duration_s = int(call.data.get("duration_s", 0))
        strip_type = int(call.data.get("strip_type", 0))
        kelvin = call.data.get("kelvin")
        r = int(call.data.get("red", 255))
        g = int(call.data.get("green", 255))
        b = int(call.data.get("blue", 255))
        if strip_type == LED_STRIP_TYPE_CCT and kelvin is not None:
            r, g = cct_warm_cool_from_kelvin(int(kelvin))
            b = 0
        args = pack_set_led_effect_args(
            effect_id if effect_id != LED_EFFECT_OFF else LED_EFFECT_OFF,
            duration_s,
            r,
            g,
            b,
            strip_index=strip_index,
            strip_type=strip_type,
        )
        wire = [module_id, 111, *args, 0, 0, 0]
        await send_can(can_v2_config_request_id(module_id), wire[:8], False, False)

    async def _handle_set_relay_link(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        src_relay = int(call.data["src_relay"])
        target_module = int(call.data["target_module_id"])
        target_relay = int(call.data["target_relay"])
        trigger_raw = call.data.get("trigger", "any")
        if isinstance(trigger_raw, int):
            trigger = int(trigger_raw)
        else:
            trigger_map = {
                "on": 1,
                "off": 2,
                "any": RELAY_LINK_TRIGGER_ANY,
                "mirror": RELAY_LINK_TRIGGER_MIRROR,
            }
            trigger = trigger_map.get(str(trigger_raw).lower(), RELAY_LINK_TRIGGER_ANY)
        try:
            target_state = resolve_relay_link_target_state(
                target_state=call.data.get("target_state", "toggle"),
                target_state_code=call.data.get("target_state_code"),
                timed_minutes=call.data.get("timed_minutes"),
                trigger=trigger,
            )
        except ValueError as err:
            _LOGGER.error("set_relay_link: %s", err)
            return
        args = pack_set_relay_link_args(
            src_relay, trigger, target_module, target_relay, target_state
        )
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_SET_RELAY_LINK, *args, 0, 0],
            False,
            False,
        )

    async def _handle_clear_relay_links(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_CLEAR_RELAY_LINKS, 0, 0, 0, 0, 0, 0],
            False,
            False,
        )

    async def _handle_set_relay_bind_route(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        button = int(call.data["button"])
        action_map = {
            "single": 1,
            "double": 2,
            "triple": 3,
            "quad": 4,
            "quint": 5,
            "long": 6,
        }
        action_raw = call.data.get("action", "single")
        action = int(action_raw) if isinstance(action_raw, int) else action_map.get(str(action_raw).lower(), 1)
        target_module = int(call.data["target_module_id"])
        relay = int(call.data["relay"])
        try:
            relay_state = resolve_relay_link_target_state(
                target_state=call.data.get("relay_state", "on"),
                target_state_code=call.data.get("relay_state_code"),
                timed_minutes=call.data.get("timed_minutes"),
                trigger=1,
            )
        except ValueError as err:
            _LOGGER.error("set_relay_bind_route: %s", err)
            return
        args = pack_set_relay_bind_route_args(button, action, target_module, relay, relay_state)
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_SET_RELAY_BIND_ROUTE, *args, 0, 0],
            False,
            False,
        )

    async def _handle_clear_relay_bind_routes(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_CLEAR_RELAY_BIND_ROUTES, 0, 0, 0, 0, 0, 0],
            False,
            False,
        )

    async def _handle_set_led_binding(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        strip_index = int(call.data.get("strip_index", 1))
        source_module = int(call.data["source_module_id"])
        button = int(call.data["button"])
        action_map = {
            "single": 1,
            "double": 2,
            "triple": 3,
            "quad": 4,
            "quint": 5,
            "long": 6,
        }
        action_raw = call.data.get("action", "single")
        action = int(action_raw) if isinstance(action_raw, int) else action_map.get(str(action_raw).lower(), 1)
        effect_id = int(call.data.get("effect_id", LED_EFFECT_SOLID))
        duration_s = int(call.data.get("duration_s", 0))
        strip_type = int(call.data.get("strip_type", LED_STRIP_TYPE_RGB))
        kelvin = call.data.get("kelvin")
        r = int(call.data.get("red", 255))
        g = int(call.data.get("green", 255))
        b = int(call.data.get("blue", 255))
        if strip_type == LED_STRIP_TYPE_CCT and kelvin is not None:
            r, g = cct_warm_cool_from_kelvin(int(kelvin))
            b = 0
        args = pack_set_led_binding_args(
            source_module,
            button,
            action,
            effect_id,
            duration_s,
            r,
            g,
            b,
            strip_index=strip_index,
            strip_type=strip_type,
            kelvin=int(kelvin) if kelvin is not None else None,
        )
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_SET_LED_BINDING, *args, 0, 0],
            False,
            False,
        )

    async def _handle_clear_led_bindings(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_CLEAR_LED_BINDINGS, 0, 0, 0, 0, 0, 0],
            False,
            False,
        )

    async def _handle_start_can_ota(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        firmware_path = str(call.data["firmware_path"])
        path = Path(firmware_path)
        if not path.is_file():
            _LOGGER.error("start_can_ota: firmware not found: %s", firmware_path)
            return
        firmware = await hass.async_add_executor_job(path.read_bytes)
        result = await upload_firmware_over_can(hass, send_can, module_id, firmware)
        if not result.get("ok"):
            _LOGGER.error("start_can_ota failed: %s", result.get("error", result))

    async def _handle_enable_ble_ota(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        enable = 1 if bool(call.data.get("enable", True)) else 0
        timeout_min = int(call.data.get("timeout_min", 30))
        erase_nvs = 1 if bool(call.data.get("erase_nvs", False)) else 0
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_BLE_OTA_ENABLE, enable, timeout_min, erase_nvs, 0, 0, 0],
            False,
            False,
        )

    async def _handle_set_ble_ota_pin(call: ServiceCall) -> None:
        module_id = int(call.data[ATTR_MODULE_ID])
        pin = str(call.data.get("pin", "")).strip()
        if not pin:
            await send_can(
                can_v2_config_request_id(module_id),
                [module_id, CONFIG_CMD_SET_BLE_OTA_PIN, 0, 0, 0, 0, 0, 0],
                False,
                False,
            )
            return
        if len(pin) < BLE_OTA_PIN_MIN_LEN or len(pin) > BLE_OTA_PIN_MAX_LEN:
            _LOGGER.error(
                "set_ble_ota_pin: PIN length must be %d..%d digits",
                BLE_OTA_PIN_MIN_LEN,
                BLE_OTA_PIN_MAX_LEN,
            )
            return
        if not pin.isdigit():
            _LOGGER.error("set_ble_ota_pin: PIN must be ASCII digits")
            return
        offset = 0
        while offset < len(pin):
            chunk = pin[offset : offset + CAN_BLE_OTA_PIN_CHUNK]
            wire = [
                module_id,
                CONFIG_CMD_SET_BLE_OTA_PIN,
                len(pin),
                offset,
                *[ord(c) for c in chunk.ljust(CAN_BLE_OTA_PIN_CHUNK, "0")[:CAN_BLE_OTA_PIN_CHUNK]],
            ]
            while len(wire) < 8:
                wire.append(0)
            await send_can(can_v2_config_request_id(module_id), wire[:8], False, False)
            offset += CAN_BLE_OTA_PIN_CHUNK
            await asyncio.sleep(0.05)

    if not hass.services.has_service(DOMAIN, SERVICE_IDENTIFY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_IDENTIFY,
            _handle_identify,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Optional("duration_s", default=5): vol.All(cv.positive_int, vol.Range(max=120)),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_RELAY_STATE,
            _handle_set_relay,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Required("relay_no"): cv.positive_int,
                    vol.Optional("state", default="toggle"): vol.In(["off", "on", "toggle"]),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SHUTTER_COMMAND,
            _handle_shutter,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Required("shutter_no"): cv.positive_int,
                    vol.Required("command"): vol.In(["open", "close", "stop", "set_position"]),
                    vol.Optional("position"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_LED_EFFECT,
            _handle_led_effect,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Optional("strip_index", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
                    vol.Optional("effect_id", default=LED_EFFECT_SOLID): vol.Coerce(int),
                    vol.Optional("duration_s", default=0): vol.Coerce(int),
                    vol.Optional("strip_type", default=0): vol.In([0, 1]),
                    vol.Optional("red"): vol.Coerce(int),
                    vol.Optional("green"): vol.Coerce(int),
                    vol.Optional("blue"): vol.Coerce(int),
                    vol.Optional("kelvin"): vol.Coerce(int),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_RELAY_LINK,
            _handle_set_relay_link,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Required("src_relay"): cv.positive_int,
                    vol.Required("target_module_id"): cv.positive_int,
                    vol.Required("target_relay"): cv.positive_int,
                    vol.Optional("trigger", default="any"): vol.Any(
                        cv.positive_int,
                        vol.In(["on", "off", "any", "mirror"]),
                    ),
                    vol.Optional("target_state", default="toggle"): vol.Any(
                        vol.In(["off", "on", "toggle"]),
                        cv.positive_int,
                    ),
                    vol.Optional("target_state_code"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                    vol.Optional("timed_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_RELAY_LINKS,
            _handle_clear_relay_links,
            schema=vol.Schema({vol.Required(ATTR_MODULE_ID): cv.positive_int}),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_RELAY_BIND_ROUTE,
            _handle_set_relay_bind_route,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Required("button"): cv.positive_int,
                    vol.Required("target_module_id"): cv.positive_int,
                    vol.Required("relay"): cv.positive_int,
                    vol.Optional("action", default="single"): vol.Any(
                        cv.positive_int,
                        vol.In(["single", "double", "triple", "quad", "quint", "long"]),
                    ),
                    vol.Optional("relay_state", default="on"): vol.Any(
                        vol.In(["off", "on", "toggle"]),
                        cv.positive_int,
                    ),
                    vol.Optional("relay_state_code"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                    vol.Optional("timed_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_RELAY_BIND_ROUTES,
            _handle_clear_relay_bind_routes,
            schema=vol.Schema({vol.Required(ATTR_MODULE_ID): cv.positive_int}),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_LED_BINDING,
            _handle_set_led_binding,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Required("source_module_id"): cv.positive_int,
                    vol.Required("button"): cv.positive_int,
                    vol.Optional("strip_index", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
                    vol.Optional("action", default="single"): vol.Any(
                        cv.positive_int,
                        vol.In(["single", "double", "triple", "quad", "quint", "long"]),
                    ),
                    vol.Optional("effect_id", default=LED_EFFECT_SOLID): vol.Coerce(int),
                    vol.Optional("duration_s", default=0): vol.Coerce(int),
                    vol.Optional("strip_type", default=0): vol.In([0, 1]),
                    vol.Optional("red"): vol.Coerce(int),
                    vol.Optional("green"): vol.Coerce(int),
                    vol.Optional("blue"): vol.Coerce(int),
                    vol.Optional("kelvin"): vol.Coerce(int),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_LED_BINDINGS,
            _handle_clear_led_bindings,
            schema=vol.Schema({vol.Required(ATTR_MODULE_ID): cv.positive_int}),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_START_CAN_OTA,
            _handle_start_can_ota,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Required("firmware_path"): cv.string,
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_ENABLE_BLE_OTA,
            _handle_enable_ble_ota,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Optional("enable", default=True): cv.boolean,
                    vol.Optional("timeout_min", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
                    vol.Optional("erase_nvs", default=False): cv.boolean,
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_BLE_OTA_PIN,
            _handle_set_ble_ota_pin,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MODULE_ID): cv.positive_int,
                    vol.Optional("pin", default=""): cv.string,
                }
            ),
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if str(entry.data.get(CONF_CONNECTION_MODE, "")).lower() != CONNECTION_MODE_ADDON:
        _LOGGER.error(
            "CAN Gateway requires connection_mode=addon — this integration is deployed "
            "exclusively by the CAN Gateway add-on. Remove this config entry and reinstall "
            "via the add-on (Supervisor discovery)."
        )
        return False

    from .addon_setup import async_setup_entry as addon_setup_entry

    return await addon_setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .addon_setup import async_unload_entry as addon_unload_entry

    return await addon_unload_entry(hass, entry)
