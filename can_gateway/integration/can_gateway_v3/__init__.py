from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .can_io import SlcanSerialBridge
from .const import (
    ATTR_MODULE_ID,
    CONF_CAN_BITRATE,
    CONF_CONNECTION_MODE,
    CONF_INITIAL_SCAN_DONE,
    CONF_SCAN_ON_SETUP,
    CONF_SERIAL_BAUDRATE,
    CONF_SERIAL_PORT,
    CONNECTION_MODE_ADDON,
    CONFIG_CMD_GET_LED_STRIP_CONFIG,
    CONFIG_CMD_GET_LED_BINDING_COUNT,
    CONFIG_CMD_GET_LED_BINDING,
    CONFIG_CMD_GET_LED_BINDING_COUNT,
    CONFIG_CMD_GET_MCP23017_ROLE_DUMP,
    CONFIG_CMD_GET_RELAY_BIND_ROUTE_COUNT,
    CONFIG_CMD_GET_RELAY_BIND_ROUTE,
    CONFIG_CMD_GET_RELAY_LINK_COUNT,
    CONFIG_CMD_GET_RELAY_LINK,
    CONFIG_CMD_GET_SHUTTER_RELAYS,
    CONFIG_CMD_SET_RELAY_BIND_ROUTE,
    CONFIG_CMD_CLEAR_RELAY_BIND_ROUTES,
    CONFIG_CMD_SET_LED_BINDING,
    CONFIG_CMD_CLEAR_LED_BINDINGS,
    CONFIG_CMD_BLE_OTA_ENABLE,
    CONFIG_CMD_SET_BLE_OTA_PIN,
    CONFIG_CMD_SET_RELAY_LINK,
    CONFIG_CMD_CLEAR_RELAY_LINKS,
    DEFAULT_CAN_BITRATE,
    DEFAULT_SERIAL_BAUDRATE,
    DEFAULT_SERIAL_PORT,
    DOMAIN,
    EVENT_CONFIG_RESPONSE,
    MAX_SHUTTER_SLOTS,
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
from .coordinator import CanGatewayCoordinator
from .led_protocol import (
    LED_EFFECT_OFF,
    LED_EFFECT_SOLID,
    LED_STRIP_TYPE_CCT,
    LED_STRIP_TYPE_RGB,
    MAX_LED_STRIPS,
    cct_warm_cool_from_kelvin,
    pack_set_led_binding_args,
    pack_set_led_effect_args,
)
from .ota_upload import upload_firmware_over_can
from .parser import events_from_payload
from .protocol import (
    BLE_OTA_PIN_MAX_LEN,
    BLE_OTA_PIN_MIN_LEN,
    CAN_BLE_OTA_PIN_CHUNK,
    COMMAND_GET_MODULE_NAME,
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
    module_name_read_offsets,
)

_LOGGER = logging.getLogger(__name__)

CORE_PLATFORMS = tuple(PLATFORMS)


async def _poll_led_bindings_for_module(
    send_can, coordinator: CanGatewayCoordinator, module_id: int, delay_s: float = 0.08
) -> None:
    await asyncio.sleep(delay_s)
    await send_can(
        can_v2_config_request_id(module_id),
        [module_id, CONFIG_CMD_GET_LED_BINDING_COUNT, 0, 0, 0, 0, 0, 0],
        False,
        False,
    )
    await asyncio.sleep(0.1)
    for idx in range(16):
        coordinator.note_led_binding_query(module_id, idx)
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_GET_LED_BINDING, idx, 0, 0, 0, 0, 0],
            False,
            False,
        )
        await asyncio.sleep(0.06)


async def _poll_relay_bind_routes_for_module(
    send_can, coordinator: CanGatewayCoordinator, module_id: int, delay_s: float = 0.08
) -> None:
    await asyncio.sleep(delay_s)
    await send_can(
        can_v2_config_request_id(module_id),
        [module_id, CONFIG_CMD_GET_RELAY_BIND_ROUTE_COUNT, 0, 0, 0, 0, 0, 0],
        False,
        False,
    )
    await asyncio.sleep(0.1)
    for idx in range(16):
        coordinator.note_relay_bind_route_query(module_id, idx)
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_GET_RELAY_BIND_ROUTE, idx, 0, 0, 0, 0, 0],
            False,
            False,
        )
        await asyncio.sleep(0.06)


async def _poll_relay_links_for_module(
    send_can, coordinator: CanGatewayCoordinator, module_id: int, delay_s: float = 0.08
) -> None:
    await asyncio.sleep(delay_s)
    await send_can(
        can_v2_config_request_id(module_id),
        [module_id, CONFIG_CMD_GET_RELAY_LINK_COUNT, 0, 0, 0, 0, 0, 0],
        False,
        False,
    )
    await asyncio.sleep(0.1)
    for idx in range(16):
        coordinator.note_relay_link_query(module_id, idx)
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_GET_RELAY_LINK, idx, 0, 0, 0, 0, 0],
            False,
            False,
        )
        await asyncio.sleep(0.06)


async def _poll_led_strips_for_module(
    send_can, coordinator: CanGatewayCoordinator, module_id: int, delay_s: float = 0.08
) -> None:
    await asyncio.sleep(delay_s)
    for strip_idx in range(1, MAX_LED_STRIPS + 1):
        coordinator.note_led_strip_query(module_id, strip_idx)
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_GET_LED_STRIP_CONFIG, strip_idx, 0, 0, 0, 0, 0],
            False,
            False,
        )
        await asyncio.sleep(0.06)


async def _poll_shutter_relay_mappings_for_module(
    send_can, module_id: int, delay_s: float = 0.12
) -> None:
    await asyncio.sleep(delay_s)
    for shutter_no in range(1, MAX_SHUTTER_SLOTS + 1):
        await send_can(
            can_v2_config_request_id(module_id),
            [module_id, CONFIG_CMD_GET_SHUTTER_RELAYS, shutter_no, 0, 0, 0, 0, 0],
            False,
            False,
        )
        await asyncio.sleep(0.08)


async def _poll_all_shutter_relay_mappings(
    send_can, coordinator: CanGatewayCoordinator
) -> None:
    await asyncio.sleep(0.55)
    for module_id in sorted(coordinator.scanned_modules):
        await _poll_shutter_relay_mappings_for_module(send_can, module_id, delay_s=0.05)


async def _send_initial_scan(send_can, rounds: int = 2, interval_s: float = 0.8) -> None:
    for _ in range(max(1, rounds)):
        await send_can(can_v2_config_request_id(0xFF), [255, 3, 0, 0, 0, 0, 0, 0], False, False)
        await asyncio.sleep(0.05)
        await send_can(can_v2_config_request_id(0xFF), [255, 3, 0, 0, 0, 0, 0, 0], False, False)
        await asyncio.sleep(0.8)
        await send_can(can_v2_config_request_id(0xFF), [255, 40, 0, 0, 0, 0, 0, 0], False, False)
        await asyncio.sleep(0.4)
        await send_can(can_v2_config_request_id(0xFF), [255, 67, 0, 0, 0, 0, 0, 0], False, False)
        await asyncio.sleep(0.4)
        await asyncio.sleep(interval_s)


async def _delayed_followup_scan(
    send_can, coordinator: CanGatewayCoordinator
) -> None:
    await asyncio.sleep(5.0)
    coordinator.mark_scan_started("followup_scan")
    for module_id in sorted(coordinator.scanned_modules):
        for cmd in (3, 24, 40, 67):
            await send_can(
                can_v2_config_request_id(module_id),
                [module_id, cmd, 0, 0, 0, 0, 0, 0],
                False,
                False,
            )
            await asyncio.sleep(0.12)
        for offset in module_name_read_offsets():
            await send_can(
                can_v2_config_request_id(module_id),
                [module_id, COMMAND_GET_MODULE_NAME, offset, 0, 0, 0, 0, 0],
                False,
                False,
            )
            await asyncio.sleep(0.055)
        for chip in range(8):
            await send_can(
                can_v2_config_request_id(module_id),
                [module_id, CONFIG_CMD_GET_MCP23017_ROLE_DUMP, chip, 0, 0, 0, 0, 0],
                False,
                False,
            )
            await asyncio.sleep(0.055)
        await _poll_led_strips_for_module(send_can, coordinator, module_id, delay_s=0.05)
        await _poll_relay_links_for_module(send_can, coordinator, module_id, delay_s=0.05)
        await _poll_relay_bind_routes_for_module(send_can, coordinator, module_id, delay_s=0.05)
        await _poll_led_bindings_for_module(send_can, coordinator, module_id, delay_s=0.05)
    await _poll_all_shutter_relay_mappings(send_can, coordinator)
    await _send_initial_scan(send_can, rounds=1, interval_s=0.6)
    coordinator.mark_scan_finished(
        "ok",
        f"Follow-up finished, modules={len(coordinator.scanned_modules)}",
    )


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
                    vol.Optional("position"): vol.All(cv.int, vol.Range(min=0, max=100)),
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
                    vol.Optional("strip_index", default=1): vol.All(cv.int, vol.Range(min=1, max=4)),
                    vol.Optional("effect_id", default=LED_EFFECT_SOLID): cv.int,
                    vol.Optional("duration_s", default=0): cv.int,
                    vol.Optional("strip_type", default=0): vol.In([0, 1]),
                    vol.Optional("red"): cv.int,
                    vol.Optional("green"): cv.int,
                    vol.Optional("blue"): cv.int,
                    vol.Optional("kelvin"): cv.int,
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
                    vol.Optional("target_state_code"): vol.All(cv.int, vol.Range(min=0, max=255)),
                    vol.Optional("timed_minutes"): vol.All(cv.int, vol.Range(min=1, max=127)),
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
                    vol.Optional("relay_state_code"): vol.All(cv.int, vol.Range(min=0, max=255)),
                    vol.Optional("timed_minutes"): vol.All(cv.int, vol.Range(min=1, max=127)),
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
                    vol.Optional("strip_index", default=1): vol.All(cv.int, vol.Range(min=1, max=4)),
                    vol.Optional("action", default="single"): vol.Any(
                        cv.positive_int,
                        vol.In(["single", "double", "triple", "quad", "quint", "long"]),
                    ),
                    vol.Optional("effect_id", default=LED_EFFECT_SOLID): cv.int,
                    vol.Optional("duration_s", default=0): cv.int,
                    vol.Optional("strip_type", default=0): vol.In([0, 1]),
                    vol.Optional("red"): cv.int,
                    vol.Optional("green"): cv.int,
                    vol.Optional("blue"): cv.int,
                    vol.Optional("kelvin"): cv.int,
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
                    vol.Optional("timeout_min", default=30): vol.All(cv.int, vol.Range(min=1, max=120)),
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
    if str(entry.data.get(CONF_CONNECTION_MODE, "")).lower() == CONNECTION_MODE_ADDON:
        from .addon_setup import async_setup_entry as addon_setup_entry

        return await addon_setup_entry(hass, entry)

    coordinator = CanGatewayCoordinator(hass)
    metadata_last_request_s: dict[int, float] = {}
    port = str(entry.data.get(CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT))
    baud = int(entry.data.get(CONF_SERIAL_BAUDRATE, DEFAULT_SERIAL_BAUDRATE))
    can_bitrate = int(entry.data.get(CONF_CAN_BITRATE, DEFAULT_CAN_BITRATE))

    async def _send_can(can_id: int, data: list[int], ext: bool = False, rtr: bool = False) -> None:
        await serial_bridge.send_frame(can_id, data, ext, rtr)

    def _request_module_metadata(module_id: int) -> None:
        info = coordinator.get_module_info(module_id)
        need_name = not bool(info.name)
        need_build = not bool(info.firmware_build_datetime)
        if not (need_name or need_build):
            return
        now_s = asyncio.get_running_loop().time()
        last_s = metadata_last_request_s.get(module_id, 0.0)
        if (now_s - last_s) < 2.0:
            return
        metadata_last_request_s[module_id] = now_s
        for cmd in (24,):
            hass.async_create_task(
                _send_can(
                    can_v2_config_request_id(module_id),
                    [module_id, cmd, 0, 0, 0, 0, 0, 0],
                    False,
                    False,
                )
            )
        for offset in module_name_read_offsets():
            hass.async_create_task(
                _send_can(
                    can_v2_config_request_id(module_id),
                    [module_id, COMMAND_GET_MODULE_NAME, offset, 0, 0, 0, 0, 0],
                    False,
                    False,
                )
            )

    def _process_raw_payload(raw_payload: str) -> None:
        try:
            events = events_from_payload(raw_payload)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Invalid CAN payload: %s", err)
            return
        for event_type, payload in events:
            coordinator.update_from_event(event_type, payload)
            hass.bus.async_fire(event_type, payload)
            module_id = payload.get("module_id")
            if isinstance(module_id, int) and 1 <= module_id <= 254:
                _request_module_metadata(module_id)
            if event_type == EVENT_CONFIG_RESPONSE:
                command = payload.get("command")
                status_code = payload.get("status_code")
                if (
                    isinstance(module_id, int)
                    and isinstance(command, int)
                    and isinstance(status_code, int)
                    and command == 3
                    and status_code == 0
                ):
                    _request_module_metadata(module_id)
                    hass.async_create_task(
                        _poll_shutter_relay_mappings_for_module(_send_can, int(module_id))
                    )
                    hass.async_create_task(
                        _poll_led_strips_for_module(_send_can, coordinator, int(module_id))
                    )
                    hass.async_create_task(
                        _poll_relay_links_for_module(_send_can, coordinator, int(module_id))
                    )
                    hass.async_create_task(
                        _poll_relay_bind_routes_for_module(_send_can, coordinator, int(module_id))
                    )
                    hass.async_create_task(
                        _poll_led_bindings_for_module(_send_can, coordinator, int(module_id))
                    )

    serial_bridge = SlcanSerialBridge(
        port=port,
        baudrate=baud,
        can_bitrate=can_bitrate,
        on_payload=_process_raw_payload,
    )
    await serial_bridge.start()
    _LOGGER.info(
        "CAN Gateway v3 using SLCAN serial port %s @ %d, CAN %d",
        port,
        baud,
        can_bitrate,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "can_send": _send_can,
        "serial_bridge": serial_bridge,
    }
    _register_services(hass, entry, _send_can)

    for platform in CORE_PLATFORMS:
        try:
            await hass.config_entries.async_forward_entry_setups(entry, (platform,))
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Platform '%s' failed to load", platform, exc_info=True)

    scan_on_setup = bool(entry.data.get(CONF_SCAN_ON_SETUP, True))
    if scan_on_setup:
        coordinator.mark_scan_started("initial_setup_scan")
        await _send_initial_scan(_send_can, rounds=2, interval_s=0.8)
        hass.async_create_task(_delayed_followup_scan(_send_can, coordinator))
        new_data = dict(entry.data)
        new_data[CONF_INITIAL_SCAN_DONE] = True
        hass.config_entries.async_update_entry(entry, data=new_data)
        coordinator.mark_scan_finished(
            "ok",
            f"Initial setup scan finished, modules={len(coordinator.scanned_modules)}",
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if str(entry.data.get(CONF_CONNECTION_MODE, "")).lower() == CONNECTION_MODE_ADDON:
        from .addon_setup import async_unload_entry as addon_unload_entry

        return await addon_unload_entry(hass, entry)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, list(CORE_PLATFORMS))
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime:
        bridge = runtime.get("serial_bridge")
        if bridge is not None:
            await bridge.stop()
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return unload_ok
