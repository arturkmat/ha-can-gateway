"""CAN protocol V3 helpers — aligned with can_gateway/lib/protocol_constants.py."""

from __future__ import annotations

CAN_V2_CLASS_CONFIG_REQUEST = 0x00
CAN_V2_CLASS_CONFIG_RESPONSE = 0x01
CAN_V2_CLASS_CONTROL_COMMAND = 0x02
CAN_V2_CLASS_OTA_DATA = 0x03
CAN_V2_CLASS_OTA_STATUS = 0x04
CAN_V2_CLASS_INPUT_EVENTS = 0x05
CAN_V2_CLASS_SENSOR_EVENTS = 0x06
CAN_V2_CLASS_STATE_TELEMETRY = 0x07

TELE_DEVICE_INFO = 1
TELE_RELAY_STATE = 2
TELE_RELAY_GPIO_MAP = 3
TELE_MCP23017_RELAY = 4
TELE_GPIO_VALUE = 5
TELE_SHUTTER_STATUS = 6
TELE_DIAGNOSTICS = 7

CAN_V3_ID_MASK_MODULE = 0x7F8
CAN_V3_ID_MASK_TYPE = 0x007
CAN_V3_BROADCAST_MODULE_ID = 0xFF

V2_CTRL_SHUTTER_CMD = 1
V2_INPUT_BUTTON_EVENT = 1

SENSOR_TYPE_NTC = 5
MODULE_NAME_MAX_LEN = 15
MODULE_NAME_CHUNK_READ = 3

COMMAND_IDENTIFY = 2
COMMAND_GET_SUMMARY = 3
COMMAND_GET_MODULE_NAME = 37
COMMAND_SET_RELAY_STATE = 59
COMMAND_OTA_BEGIN = 60
COMMAND_OTA_SET_TIMESTAMP = 61
COMMAND_OTA_END = 62
COMMAND_OTA_ABORT = 63
COMMAND_OTA_GET_INFO = 64
COMMAND_SET_RELAY_BIND_ROUTE = 85
COMMAND_CLEAR_RELAY_BIND_ROUTES = 86
COMMAND_GET_RELAY_BIND_ROUTE_COUNT = 87
COMMAND_GET_RELAY_BIND_ROUTE = 88
COMMAND_BLE_OTA_ENABLE = 103
COMMAND_SET_BLE_OTA_PIN = 104
COMMAND_GET_BLE_OTA_PIN_STATE = 105
COMMAND_SET_LED_BINDING = 112
COMMAND_CLEAR_LED_BINDINGS = 113
COMMAND_GET_LED_STRIP_CONFIG = 110
COMMAND_SET_LED_EFFECT = 111
COMMAND_GET_LED_BINDING_COUNT = 114
COMMAND_GET_LED_BINDING = 115
COMMAND_SET_RELAY_LINK = 116
COMMAND_CLEAR_RELAY_LINKS = 117
COMMAND_GET_RELAY_LINK_COUNT = 118
COMMAND_GET_RELAY_LINK = 119
COMMAND_REBOOT = 58

BIND_RELAY_STATE_TIMED_MIN = 128
OTA_PAYLOAD_BYTES = 5
OTA_BATCH_FRAMES = 64
OTA_STATUS_READY = 0
OTA_STATUS_NACK = 2
OTA_STATUS_DONE = 3
OTA_STATUS_ERROR = 4
BLE_OTA_PIN_MIN_LEN = 4
BLE_OTA_PIN_MAX_LEN = 8
CAN_BLE_OTA_PIN_CHUNK = 4

RELAY_LINK_TRIGGER_ON = 1
RELAY_LINK_TRIGGER_OFF = 2
RELAY_LINK_TRIGGER_ANY = 3
RELAY_LINK_TRIGGER_MIRROR = 4

RELAY_LINK_TRIGGER_NAMES = {
    RELAY_LINK_TRIGGER_ON: "on",
    RELAY_LINK_TRIGGER_OFF: "off",
    RELAY_LINK_TRIGGER_ANY: "any",
    RELAY_LINK_TRIGGER_MIRROR: "mirror",
}

RELAY_STATE_LABELS = {0: "off", 1: "on", 2: "toggle"}

BUTTON_ACTION_NAMES = {
    1: "single",
    2: "double",
    3: "triple",
    4: "quad",
    5: "quint",
    6: "long",
}

SHUTTER_CMD_OPEN = 1
SHUTTER_CMD_CLOSE = 2
SHUTTER_CMD_STOP = 3
SHUTTER_CMD_SET_POSITION = 4


def can_v2_frame_id(frame_class: int, module_id: int) -> int:
    return ((int(module_id) & 0xFF) << 3) | (int(frame_class) & 0x07)


def can_v2_frame_class(arbitration_id: int) -> int:
    return int(arbitration_id) & CAN_V3_ID_MASK_TYPE


def can_v2_frame_module_id(arbitration_id: int) -> int:
    return (int(arbitration_id) & CAN_V3_ID_MASK_MODULE) >> 3


def can_v2_input_events_broadcast_id() -> int:
    return can_v2_frame_id(CAN_V2_CLASS_INPUT_EVENTS, CAN_V3_BROADCAST_MODULE_ID)


def can_v2_config_request_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONFIG_REQUEST, module_id)


def can_v2_config_response_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONFIG_RESPONSE, module_id)


def can_v2_control_command_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONTROL_COMMAND, module_id)


def can_v2_ota_data_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_OTA_DATA, module_id)


def can_v2_ota_status_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_OTA_STATUS, module_id)


def module_name_read_offsets() -> list[int]:
    return list(range(0, MODULE_NAME_MAX_LEN, MODULE_NAME_CHUNK_READ))


def is_config_response_frame(can_id: int) -> bool:
    return can_v2_frame_class(can_id) == CAN_V2_CLASS_CONFIG_RESPONSE


def pack_set_relay_link_args(
    src_relay: int,
    trigger: int,
    target_module: int,
    target_relay: int,
    target_state: int,
) -> list[int]:
    return [
        int(src_relay) & 0xFF,
        int(trigger) & 0xFF,
        int(target_module) & 0xFF,
        int(target_relay) & 0xFF,
        int(target_state) & 0xFF,
    ]


def unpack_get_relay_link_fields(response_data: list[int]) -> tuple[int, int, int, int, int]:
    """Decode GET_RELAY_LINK (119) response fields from data[3..7]."""
    if len(response_data) < 5:
        raise ValueError("GET_RELAY_LINK response too short")
    return (
        int(response_data[0]) & 0xFF,
        int(response_data[1]) & 0xFF,
        int(response_data[2]) & 0xFF,
        int(response_data[3]) & 0xFF,
        int(response_data[4]) & 0xFF,
    )


def format_relay_link_target_state(target_state: int, trigger: int) -> str:
    if int(trigger) == RELAY_LINK_TRIGGER_MIRROR:
        return "mirror"
    code = int(target_state)
    if code >= BIND_RELAY_STATE_TIMED_MIN:
        return f"timed_{code - BIND_RELAY_STATE_TIMED_MIN}m"
    return RELAY_STATE_LABELS.get(code, str(code))


def resolve_relay_link_target_state(
    *,
    target_state: str | int | None = None,
    target_state_code: int | None = None,
    timed_minutes: int | None = None,
    trigger: int = RELAY_LINK_TRIGGER_ANY,
) -> int:
    """Map service fields to wire target_state (incl. timed 128+)."""
    if int(trigger) == RELAY_LINK_TRIGGER_MIRROR:
        return 0
    if target_state_code is not None:
        return int(target_state_code) & 0xFF
    if timed_minutes is not None and int(timed_minutes) > 0:
        mins = int(timed_minutes)
        if mins < 1 or mins > 127:
            raise ValueError("timed_minutes must be 1..127")
        return BIND_RELAY_STATE_TIMED_MIN + mins
    if isinstance(target_state, int):
        return int(target_state) & 0xFF
    state_map = {"off": 0, "on": 1, "toggle": 2}
    return state_map.get(str(target_state or "toggle").lower(), 2)


def pack_set_relay_bind_route_args(
    button: int,
    action: int,
    target_module: int,
    relay: int,
    relay_state: int,
) -> list[int]:
    return [
        int(button) & 0xFF,
        int(action) & 0xFF,
        int(target_module) & 0xFF,
        int(relay) & 0xFF,
        int(relay_state) & 0xFF,
    ]


def unpack_get_relay_bind_route_fields(response_data: list[int]) -> tuple[int, int, int, int, int]:
    if len(response_data) < 5:
        raise ValueError("GET_RELAY_BIND_ROUTE response too short")
    return (
        int(response_data[0]) & 0xFF,
        int(response_data[1]) & 0xFF,
        int(response_data[2]) & 0xFF,
        int(response_data[3]) & 0xFF,
        int(response_data[4]) & 0xFF,
    )


def format_relay_bind_route_summary(
    button: int,
    action: int,
    target_module: int,
    relay: int,
    relay_state: int,
) -> str:
    act = BUTTON_ACTION_NAMES.get(int(action), str(action))
    tgt = f"M{int(target_module)} R{int(relay)}"
    state = format_relay_link_target_state(relay_state, RELAY_LINK_TRIGGER_ON)
    return f"btn{int(button)} {act} → {tgt} {state}"


def format_relay_link_summary(
    src_relay: int,
    trigger: int,
    target_module: int,
    target_relay: int,
    target_state: int,
) -> str:
    trig = RELAY_LINK_TRIGGER_NAMES.get(int(trigger), str(trigger))
    tgt = f"M{int(target_module)} R{int(target_relay)}"
    if int(trigger) == RELAY_LINK_TRIGGER_MIRROR:
        return f"R{int(src_relay)} {trig} → {tgt}"
    state = format_relay_link_target_state(target_state, trigger)
    return f"R{int(src_relay)} {trig} → {tgt} {state}"
