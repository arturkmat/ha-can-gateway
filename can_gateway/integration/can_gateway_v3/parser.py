from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .const import (
    EVENT_BUTTON,
    EVENT_CONFIG_REQUEST,
    EVENT_CONFIG_RESPONSE,
    EVENT_DEVICE_INFO,
    EVENT_DIAG,
    EVENT_FRAME,
    EVENT_GPIO,
    EVENT_OTA_DATA,
    EVENT_OTA_STATUS,
    EVENT_RELAY,
    EVENT_RELAY_GPIO_MAP,
    EVENT_RELAY_MCP23017,
    EVENT_SENSOR,
    EVENT_SHUTTER_COMMAND,
    EVENT_SHUTTER,
    MCP23017_RELAY_CAN_BASE,
    MCP23017_RELAY_ENTITY_BASE,
)
from .protocol import (
    CAN_V2_CLASS_CONFIG_REQUEST,
    CAN_V2_CLASS_CONFIG_RESPONSE,
    CAN_V2_CLASS_CONTROL_COMMAND,
    CAN_V2_CLASS_INPUT_EVENTS,
    CAN_V2_CLASS_OTA_DATA,
    CAN_V2_CLASS_OTA_STATUS,
    CAN_V2_CLASS_SENSOR_EVENTS,
    CAN_V2_CLASS_STATE_TELEMETRY,
    MODULE_NAME_MAX_LEN,
    SENSOR_TYPE_NTC,
    TELE_DEVICE_INFO,
    TELE_DIAGNOSTICS,
    TELE_GPIO_VALUE,
    TELE_MCP23017_RELAY,
    TELE_RELAY_GPIO_MAP,
    TELE_RELAY_STATE,
    TELE_SHUTTER_STATUS,
    V2_CTRL_SHUTTER_CMD,
    V2_INPUT_BUTTON_EVENT,
    can_v2_frame_class,
    can_v2_frame_module_id,
)


BUTTON_ACTIONS = {
    1: "single",
    2: "double",
    3: "triple",
    4: "quad",
    5: "quint",
    6: "long",
}

SENSOR_TYPES = {
    1: "ds18b20",
    2: "bme280",
    3: "sht30",
    4: "bme280_pressure",
    SENSOR_TYPE_NTC: "ntc",
}

CONFIG_STATUS = {
    0: "ok",
    1: "unsupported",
    2: "invalid_argument",
    3: "full",
    4: "not_found",
}

SHUTTER_COMMANDS = {
    1: "open",
    2: "close",
    3: "stop",
    4: "set_position",
}

SHUTTER_DIRECTIONS = {
    0: "stopped",
    1: "opening",
    2: "closing",
}

OTA_STATUS = {
    0: "ready",
    1: "progress",
    2: "nack",
    3: "done",
    4: "error",
}

CONFIG_COMMANDS = {
    1: "set_module_id",
    2: "identify",
    3: "get_summary",
    4: "set_module_id_by_mac",
    16: "set_binding",
    17: "clear_bindings",
    18: "set_button_timing",
    19: "get_button_timing",
    22: "get_binding",
    23: "get_binding_count",
    24: "get_build_info",
    32: "set_gpio_role",
    33: "clear_gpio_role",
    34: "clear_all_gpio_roles",
    35: "get_gpio_role",
    36: "set_module_name",
    37: "get_module_name",
    38: "get_gpio_value",
    39: "set_service_mode",
    40: "scan_sensors",
    41: "set_shutter_time",
    42: "get_shutter_time",
    43: "set_shutter_time_open",
    44: "get_shutter_time_open",
    45: "set_shutter_time_close",
    46: "get_shutter_time_close",
    47: "set_shutter_mapping",
    48: "clear_shutter_mappings",
    49: "set_shutter_relays",
    50: "get_shutter_relays",
    51: "delete_binding",
    52: "get_shutter_binding_count",
    53: "get_shutter_binding",
    54: "set_shift595_flags",
    55: "get_shift595_flags",
    56: "scan_1wire",
    57: "scan_i2c",
    58: "reboot",
    59: "set_relay_state",
    60: "ota_begin",
    61: "ota_set_timestamp",
    62: "ota_end",
    63: "ota_abort",
    64: "ota_get_info",
    65: "set_mcp23017_pin_role",
    66: "get_mcp23017_pin_role",
    67: "scan_mcp23017",
    68: "get_mcp23017_input_state",
    69: "can_mute",
    70: "get_mcp23017_role_dump",
    71: "get_shift595_dump",
    72: "set_relay_pulse",
    73: "get_relay_pulse",
    85: "set_relay_bind_route",
    86: "clear_relay_bind_routes",
    87: "get_relay_bind_route_count",
    88: "get_relay_bind_route",
    103: "ble_ota_enable",
    104: "set_ble_ota_pin",
    105: "get_ble_ota_pin_state",
    109: "set_led_strip_config",
    110: "get_led_strip_config",
    111: "set_led_effect",
    112: "set_led_binding",
    113: "clear_led_bindings",
    114: "get_led_binding_count",
    115: "get_led_binding",
    116: "set_relay_link",
    117: "clear_relay_links",
    118: "get_relay_link_count",
    119: "get_relay_link",
}

HW_TYPES = {
    1: "esp32-wroom-32e",
    2: "xiao-esp32-c6",
    3: "waveshare-esp32-c6-zero",
    4: "wemos-d1-mini-esp32",
    255: "unknown",
}

# Najwyzszy fizyczny numer GPIO (ESP32-S3 = 48) oraz maks. liczba rolet (firmware).
MAX_GPIO_NUM = 48
MAX_SHUTTERS = 28


def _u16_le(data: list[int], offset: int) -> int:
    return int.from_bytes(bytes(data[offset: offset + 2]), byteorder="little", signed=False)


def _u24_le(data: list[int], offset: int) -> int:
    b0 = data[offset] if offset < len(data) else 0
    b1 = data[offset + 1] if offset + 1 < len(data) else 0
    b2 = data[offset + 2] if offset + 2 < len(data) else 0
    return b0 | (b1 << 8) | (b2 << 16)


def _u32_le(data: list[int], offset: int) -> int:
    return int.from_bytes(bytes(data[offset: offset + 4]), byteorder="little", signed=False)


def _decode_config_request_args(command: int, args: list[int]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if command == 1 and len(args) >= 1:
        parsed["new_module_id"] = args[0]
    elif command == 2 and len(args) >= 1:
        parsed["identify_seconds"] = args[0]
    elif command == 4 and len(args) >= 6:
        parsed["mac"] = ":".join(f"{part:02X}" for part in args[:6])
    elif command in (39, 69) and len(args) >= 2:
        parsed["enable"] = bool(args[0])
        parsed["timeout_s"] = args[1]
    elif command in (41, 43, 45) and len(args) >= 3:
        parsed["shutter_no"] = args[0]
        parsed["time_ds"] = _u16_le(args, 1)
    elif command == 47 and len(args) >= 5:
        parsed["source_module_id"] = args[0]
        parsed["button_no"] = args[1]
        parsed["action_type"] = args[2]
        parsed["shutter_no"] = args[3]
        parsed["shutter_command"] = args[4]
    elif command == 49 and len(args) >= 3:
        parsed["shutter_no"] = args[0]
        parsed["relay_open"] = args[1]
        parsed["relay_close"] = args[2]
    elif command == 59 and len(args) >= 2:
        parsed["relay_no"] = args[0]
        parsed["state_code"] = args[1]
        parsed["state"] = {0: "off", 1: "on", 2: "toggle"}.get(args[1], "unknown")
    elif command == 60 and len(args) >= 5:
        parsed["firmware_size"] = _u32_le(args, 0)
        parsed["batch_size"] = args[4]
    elif command in (61, 62) and len(args) >= 4:
        parsed["value"] = _u32_le(args, 0)
    elif command == 65 and len(args) >= 3:
        parsed["virtual_pin"] = args[0]
        parsed["role"] = args[1]
        parsed["flags"] = args[2]
    elif command == 72 and len(args) >= 3:
        parsed["relay_no"] = args[0]
        parsed["pulse_ms"] = _u16_le(args, 1)
    return parsed


def _decode_config_response_data(command: int, response_data: list[int]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if command == 3 and len(response_data) >= 4:
        parsed["button_count"] = response_data[0]
        parsed["relay_count"] = response_data[1]
        parsed["has_ds18b20"] = bool(response_data[2])
        parsed["shutter_count"] = response_data[3]
        if len(response_data) >= 5:
            parsed["hw_flags"] = response_data[4]
    elif command == 19 and len(response_data) >= 2:
        parsed["multiclick_ms"] = response_data[0] * 10
        parsed["longpress_ms"] = response_data[1] * 10
    elif command == 24 and len(response_data) >= 5:
        parsed["build_year"] = 2000 + response_data[0]
        parsed["build_month"] = response_data[1]
        parsed["build_day"] = response_data[2]
        parsed["build_hour"] = response_data[3]
        parsed["build_minute"] = response_data[4]
    elif command == 35 and len(response_data) >= 4:
        parsed["role"] = response_data[0]
        parsed["index"] = response_data[1]
        parsed["flags"] = response_data[2]
        parsed["valid"] = bool(response_data[3])
    elif command == 37 and len(response_data) >= 2 and response_data[0] <= MODULE_NAME_MAX_LEN:
        parsed["total_len"] = response_data[0]
        parsed["offset"] = response_data[1]
        chars = [value for value in response_data[2:5] if value != 0]
        parsed["name_chunk"] = bytes(chars).decode("ascii", errors="ignore")
    elif command == 38 and len(response_data) >= 4:
        parsed["logical"] = response_data[0]
        parsed["raw"] = response_data[1]
        parsed["pin_role"] = response_data[2]
        parsed["valid"] = bool(response_data[3])
    elif command == 40 and len(response_data) >= 5:
        parsed["flags"] = response_data[0]
        parsed["ds18_gpio_or_count"] = response_data[1]
        parsed["i2c_sda"] = response_data[2]
        parsed["i2c_scl"] = response_data[3]
        parsed["addr_flags"] = response_data[4]
    elif command in (56, 57):
        if response_data:
            parsed["flags"] = response_data[0]
        if len(response_data) >= 2:
            parsed["value1"] = response_data[1]
        if len(response_data) >= 3:
            parsed["value2"] = response_data[2]
        if len(response_data) >= 4:
            parsed["value3"] = response_data[3]
        if len(response_data) >= 5:
            parsed["value4"] = response_data[4]
    elif command == 50 and len(response_data) >= 3:
        parsed["shutter_no"] = response_data[0]
        parsed["relay_open"] = response_data[1]
        parsed["relay_close"] = response_data[2]
    elif command == 64 and len(response_data) >= 5:
        parsed["ota_state"] = response_data[0]
        parsed["last_epoch"] = _u32_le(response_data, 1)
    elif command == 87 and len(response_data) >= 2:
        parsed["route_count"] = int(response_data[0])
        parsed["route_max"] = int(response_data[1])
    elif command == 88 and len(response_data) >= 5:
        from .protocol import unpack_get_relay_bind_route_fields

        try:
            btn, act, tgt_mod, rly, st = unpack_get_relay_bind_route_fields(response_data)
            parsed.update(
                {
                    "button": btn,
                    "action": act,
                    "target_module": tgt_mod,
                    "relay": rly,
                    "relay_state": st,
                }
            )
        except ValueError:
            pass
    elif command == 105 and len(response_data) >= 1:
        parsed["pin_configured"] = bool(int(response_data[0]))
    elif command == 70 and len(response_data) >= 5:
        parsed["chip_offset"] = response_data[0]
        parsed["roles_packed"] = response_data[1:5]
    elif command == 71 and len(response_data) >= 5:
        parsed["register_count"] = response_data[0]
        parsed["data_gpio"] = response_data[1]
        parsed["clock_gpio"] = response_data[2]
        parsed["latch_gpio"] = response_data[3]
        parsed["oe_gpio"] = response_data[4]
    elif command == 73 and len(response_data) >= 3:
        parsed["relay_no"] = response_data[0]
        parsed["pulse_ms"] = _u16_le(response_data, 1)
    elif command == 110 and len(response_data) >= 4:
        from .led_protocol import unpack_get_led_strip_config_response

        try:
            led = unpack_get_led_strip_config_response([0, command, 0, *response_data])
            parsed.update(led)
        except ValueError:
            pass
    elif command == 114 and len(response_data) >= 2:
        parsed["binding_count"] = int(response_data[0])
        parsed["binding_max"] = int(response_data[1])
    elif command == 115 and len(response_data) >= 5:
        from .led_protocol import unpack_get_led_binding_response

        try:
            led = unpack_get_led_binding_response([0, command, 0, *response_data])
            parsed.update({k: v for k, v in led.items() if k != "status"})
        except ValueError:
            pass
    elif command == 118 and len(response_data) >= 2:
        parsed["link_count"] = int(response_data[0])
        parsed["link_max"] = int(response_data[1])
    elif command == 119 and len(response_data) >= 5:
        from .protocol import unpack_get_relay_link_fields

        try:
            src, trig, tgt_mod, tgt_rly, tgt_state = unpack_get_relay_link_fields(response_data)
            parsed.update(
                {
                    "src_relay": src,
                    "trigger": trig,
                    "target_module": tgt_mod,
                    "target_relay": tgt_rly,
                    "target_state": tgt_state,
                }
            )
        except ValueError:
            pass
    return parsed


def _payload_module_id(data: list[int], route_module_id: int) -> int:
    if data and 1 <= data[0] <= 254:
        return data[0]
    return route_module_id


def _is_sensor_payload(data: list[int]) -> bool:
    return len(data) >= 7 and data[2] in SENSOR_TYPES


def _decode_sensor_payload(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    sensor_type = data[2]
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    payload: dict[str, Any] = {
        **base,
        "sensor_no": data[1],
        "sensor_type_code": sensor_type,
        "sensor_type": SENSOR_TYPES.get(sensor_type, "unknown"),
    }
    if sensor_type in (1, SENSOR_TYPE_NTC):
        value = int.from_bytes(bytes(data[3:7]), byteorder="little", signed=True) / 100.0
        payload["temperature_c"] = value
    elif sensor_type in (2, 3):
        temp = int.from_bytes(bytes(data[3:5]), byteorder="little", signed=True) / 100.0
        hum = int.from_bytes(bytes(data[5:7]), byteorder="little", signed=False) / 100.0
        payload["temperature_c"] = temp
        payload["humidity_pct"] = hum
    elif sensor_type == 4:
        payload["pressure_pa"] = int.from_bytes(bytes(data[3:7]), byteorder="little", signed=False)
    return [(EVENT_SENSOR, payload)]


def _decode_relay_bitmap(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    relays: list[dict[str, Any]] = []
    relay_no = 1
    for byte in data[1:]:
        for bit in range(8):
            relays.append({"relay_no": relay_no, "state": "ON" if (byte >> bit) & 0x01 else "OFF"})
            relay_no += 1
    return [(EVENT_RELAY, {**base, "relays": relays})]


def _decode_gpio_payload(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    return [
        (
            EVENT_GPIO,
            {
                **base,
                "gpio": data[1],
                "logical": data[2],
                "raw": data[3],
                "role": data[4],
                "index": data[5],
                "valid": data[6],
            },
        )
    ]


def _decode_relay_gpio_map(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    relay_map: list[dict[str, Any]] = []
    start_relay = data[1]
    for i, gpio in enumerate(data[2:]):
        relay_map.append(
            {
                "relay_no": start_relay + i,
                "gpio": gpio,
                "configured": gpio != 255,
            }
        )
    return [(EVENT_RELAY_GPIO_MAP, {**base, "start_relay": start_relay, "relay_map": relay_map})]


def _decode_mcp23017_payload(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    chip_offset = data[1]
    gpa = data[2]
    gpb = data[3]
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    relays: list[dict[str, Any]] = []
    relay_base = MCP23017_RELAY_CAN_BASE + chip_offset * 16
    relay_entity_base = MCP23017_RELAY_ENTITY_BASE + chip_offset * 16
    for local_pin in range(16):
        on = ((gpa >> local_pin) & 1) if local_pin < 8 else ((gpb >> (local_pin - 8)) & 1)
        relays.append(
            {
                "relay_no": relay_base + local_pin,
                "relay_entity_no": relay_entity_base + local_pin,
                "local_pin": local_pin,
                "state": "ON" if on else "OFF",
            }
        )
    return [
        (
            EVENT_RELAY_MCP23017,
            {
                **base,
                "chip_offset": chip_offset,
                "i2c_address": 0x20 + chip_offset,
                "gpa_logical": gpa,
                "gpb_logical": gpb,
                "relays": relays,
            },
        )
    ]


def _decode_shutter_status(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    return [
        (
            EVENT_SHUTTER,
            {
                **base,
                "shutter_no": data[1],
                "position": data[2],
                "direction": data[3],
                "direction_text": SHUTTER_DIRECTIONS.get(data[3], "unknown"),
            },
        )
    ]


def _decode_device_info(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    mac = ":".join(f"{part:02X}" for part in data[2:8])
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    return [
        (
            EVENT_DEVICE_INFO,
            {
                **base,
                "hw_type": data[1],
                "hw_name": HW_TYPES.get(data[1], "unknown"),
                "mac": mac,
            },
        )
    ]


def _decode_diag(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    return [
        (
            EVENT_DIAG,
            {
                **base,
                "fw_major": data[1],
                "fw_minor": data[2],
                "build_year": 2000 + data[3],
                "build_month": data[4],
                "build_day": data[5],
                "build_hour": data[6],
                "build_minute": data[7],
            },
        )
    ]


def _decode_config_request_event(frame_id: int, data: list[int]) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 2:
        return []
    module_id = _payload_module_id(data, can_v2_frame_module_id(frame_id))
    command = data[1]
    args = data[2:]
    return [
        (
            EVENT_CONFIG_REQUEST,
            {
                "module_id": module_id,
                "can_id": frame_id,
                "data": data,
                "target_id": data[0],
                "command": command,
                "command_name": CONFIG_COMMANDS.get(command, "unknown"),
                "args": args,
                "args_decoded": _decode_config_request_args(command, args),
            },
        )
    ]


def _decode_config_response_event(frame_id: int, data: list[int]) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 3:
        return []
    module_id = _payload_module_id(data, can_v2_frame_module_id(frame_id))
    command = data[1]
    status = data[2]
    response_data = data[3:]
    return [
        (
            EVENT_CONFIG_RESPONSE,
            {
                "module_id": module_id,
                "can_id": frame_id,
                "data": data,
                "command": command,
                "command_name": CONFIG_COMMANDS.get(command, "unknown"),
                "status_code": status,
                "status": CONFIG_STATUS.get(status, "unknown"),
                "response_data": response_data,
                "response_decoded": _decode_config_response_data(command, response_data),
            },
        )
    ]


def _decode_ota_data(frame_id: int, data: list[int]) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 3:
        return []
    seq = _u24_le(data, 0)
    return [
        (
            EVENT_OTA_DATA,
            {
                "can_id": frame_id,
                "data": data,
                "seq": seq,
                "chunk": data[3:],
                "chunk_len": max(0, len(data) - 3),
            },
        )
    ]


def _decode_ota_status(frame_id: int, data: list[int], module_id: int) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 5:
        return []
    status_code = data[1] if can_v2_frame_class(frame_id) == CAN_V2_CLASS_OTA_STATUS else data[1]
    seq_offset = 2
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    seq = _u24_le(data, seq_offset)
    return [
        (
            EVENT_OTA_STATUS,
            {
                **base,
                "status_code": status_code,
                "status": OTA_STATUS.get(status_code, "unknown"),
                "seq": seq,
                "info": data[5:8],
            },
        )
    ]


def _decode_state_telemetry(frame_id: int, data: list[int], route_module_id: int) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 1:
        return []
    module_id = route_module_id
    subtype = data[0]
    if subtype == TELE_GPIO_VALUE and len(data) == 7:
        return _decode_gpio_payload(frame_id, data, module_id)
    if subtype == TELE_SHUTTER_STATUS and len(data) >= 8:
        return _decode_shutter_status(frame_id, data, module_id)
    if subtype == TELE_RELAY_GPIO_MAP and len(data) >= 8:
        return _decode_relay_gpio_map(frame_id, data[:8], module_id)
    if subtype == TELE_DIAGNOSTICS and len(data) >= 8:
        return _decode_diag(frame_id, data, module_id)
    if subtype == TELE_DEVICE_INFO and len(data) >= 8:
        return _decode_device_info(frame_id, data, module_id)
    if subtype == TELE_MCP23017_RELAY and len(data) == 4:
        return _decode_mcp23017_payload(frame_id, data[:4], module_id)
    if subtype == TELE_RELAY_STATE and len(data) >= 2:
        return _decode_relay_bitmap(frame_id, data, module_id)
    return []


def _decode_sensor_events(frame_id: int, data: list[int], route_module_id: int) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 2:
        return []
    module_id = _payload_module_id(data, route_module_id)
    if _is_sensor_payload(data):
        return _decode_sensor_payload(frame_id, data, module_id)
    return _decode_relay_bitmap(frame_id, data, module_id)


def _decode_input_events(frame_id: int, data: list[int], route_module_id: int) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 3:
        return []
    if data[0] == V2_INPUT_BUTTON_EVENT and len(data) >= 4:
        module_id = data[1]
        button_no = data[2]
        action_code = data[3]
    elif data[0] == V2_INPUT_BUTTON_EVENT:
        module_id = route_module_id
        button_no = data[1]
        action_code = data[2]
    else:
        module_id = _payload_module_id(data, route_module_id)
        button_no = data[1]
        action_code = data[2]
    base = {"module_id": module_id, "can_id": frame_id, "data": data}
    return [
        (
            EVENT_BUTTON,
            {
                **base,
                "button_no": button_no,
                "action_code": action_code,
                "action": BUTTON_ACTIONS.get(action_code, "unknown"),
            },
        )
    ]


def _decode_control_command(frame_id: int, data: list[int], route_module_id: int) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 3:
        return []
    if data[0] == V2_CTRL_SHUTTER_CMD and len(data) >= 4:
        module_id = route_module_id
        cmd = data[2]
        base = {"module_id": module_id, "can_id": frame_id, "data": data}
        return [
            (
                EVENT_SHUTTER_COMMAND,
                {
                    **base,
                    "shutter_no": data[1],
                    "command_code": cmd,
                    "command": SHUTTER_COMMANDS.get(cmd, "unknown"),
                    "param": data[3],
                },
            )
        ]
    if data[1] in CONFIG_COMMANDS and data[2] <= 4:
        return _decode_config_response_event(frame_id, data)
    return []


def _decode_v2_frame(frame_id: int, data: list[int]) -> list[tuple[str, dict[str, Any]]]:
    if len(data) < 1:
        return []
    frame_class = can_v2_frame_class(frame_id)
    route_module_id = can_v2_frame_module_id(frame_id)

    if frame_class == CAN_V2_CLASS_INPUT_EVENTS:
        return _decode_input_events(frame_id, data, route_module_id)
    if frame_class == CAN_V2_CLASS_SENSOR_EVENTS:
        return _decode_sensor_events(frame_id, data, route_module_id)
    if frame_class == CAN_V2_CLASS_STATE_TELEMETRY:
        return _decode_state_telemetry(frame_id, data, route_module_id)
    if frame_class == CAN_V2_CLASS_CONTROL_COMMAND:
        return _decode_control_command(frame_id, data, route_module_id)
    if frame_class == CAN_V2_CLASS_CONFIG_REQUEST:
        return _decode_config_request_event(frame_id, data)
    if frame_class == CAN_V2_CLASS_CONFIG_RESPONSE:
        return _decode_config_response_event(frame_id, data)
    if frame_class == CAN_V2_CLASS_OTA_DATA:
        return _decode_ota_data(frame_id, data)
    if frame_class == CAN_V2_CLASS_OTA_STATUS:
        module_id = _payload_module_id(data, route_module_id)
        return _decode_ota_status(frame_id, data, module_id)
    return []


@dataclass(slots=True)
class ParsedFrame:
    frame: dict[str, Any]
    events: list[tuple[str, dict[str, Any]]]


def parse_payload(payload: str) -> ParsedFrame:
    obj = json.loads(payload)
    if not isinstance(obj, dict):
        raise ValueError("Payload must be an object")

    frame_id = int(obj.get("id"))
    dlc = int(obj.get("dlc", 0))
    data = obj.get("data", [])
    if not isinstance(data, list):
        raise ValueError("data must be a list")

    data_bytes = [int(x) & 0xFF for x in data[:8]]
    if dlc < 0:
        dlc = 0
    if dlc > 8:
        dlc = 8
    while len(data_bytes) < dlc:
        data_bytes.append(0)

    frame = {
        "id": frame_id,
        "ext": int(obj.get("ext", 0)),
        "rtr": int(obj.get("rtr", 0)),
        "dlc": dlc,
        "data": data_bytes[:dlc],
    }
    return ParsedFrame(frame=frame, events=decode_frame(frame_id, data_bytes[:dlc]))


def decode_frame(frame_id: int, data: list[int]) -> list[tuple[str, dict[str, Any]]]:
    return _decode_v2_frame(frame_id, data)


def events_from_payload(payload: str) -> list[tuple[str, dict[str, Any]]]:
    parsed = parse_payload(payload)
    return [(EVENT_FRAME, parsed.frame), *parsed.events]
