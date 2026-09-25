"""CAN protocol constants shared by configurator and tools — V3 transport.

V3 wire layout: ``can_id = (module_id << 3) | frame_type`` as a standard 11-bit
frame (``is_extended_id=False``). All legacy fixed 29-bit / flat 0x1CE0xxxx IDs
have been removed; build arbitration IDs with the ``can_v2_*`` helpers below.
This mirrors ``konfigurator_windows_usb_can/protocol_constants.py`` and the
firmware ``can_protocol.h`` so every layer agrees.
"""

from protocol_opcodes_gen import *  # noqa: F403,F401  # generated from protocol/commands.yaml

from pathlib import Path as _Path
import sys as _sys
_repo = _Path(__file__).resolve().parents[3]
if not (_repo / "protocol" / "commands.yaml").is_file():
    _repo = _Path(__file__).resolve().parents[2]
if str(_repo) not in _sys.path:
    _sys.path.insert(0, str(_repo))
from protocol.pack import (  # noqa: E402 — shared packers (phase 3)
    BIND_FLAG_TIMED_SEC,
    BIND_FLAG_USE_RELAY_PULSE,
    BIND_RELAY_STATE_TIMED_MIN,
    BIND_RELAY_STATE_USE_PULSE,
    BINDING_FLAG_TIMED,
    BINDING_FLAG_USE_RELAY_PULSE,
    assert_tof_requires_timed_state,
    binding_state_label_to_wire,
    binding_state_wire_to_label,
    build_shutter_control_payload,
    button_relay_command_for_target,
    can_v2_config_request_id_for_command as _pack_config_req_id_for_command,
    can_v2_control_command_id_for_pc_shutter,
    format_binding_state_label,
    pack_set_binding_args,
    pack_set_relay_link_args,
    pack_set_sensor_bind_route_args,
    parse_binding_state_label,
    timed_relay_state_wire,
    unpack_set_binding_arg5,
    validate_sensor_bind_route_state,
)


# Frame types (0..7) — the low 3 bits of the 11-bit arbitration ID.
CAN_V2_CLASS_CONFIG_REQUEST = 0x00
CAN_V2_CLASS_CONFIG_RESPONSE = 0x01
CAN_V2_CLASS_CONTROL_COMMAND = 0x02
CAN_V2_CLASS_OTA_DATA = 0x03
CAN_V2_CLASS_OTA_STATUS = 0x04
CAN_V2_CLASS_INPUT_EVENTS = 0x05
CAN_V2_CLASS_SENSOR_EVENTS = 0x06
CAN_V2_CLASS_STATE_TELEMETRY = 0x07

# STATE_TELEMETRY payload[0] subtypes (module_id is in the CAN arbitration id).
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


def can_v2_frame_id(frame_class: int, module_id: int) -> int:
    """V3: (module_id << 3) | frame_type."""
    return ((int(module_id) & 0xFF) << 3) | (int(frame_class) & 0x07)


def can_v2_frame_class(arbitration_id: int) -> int:
    return int(arbitration_id) & CAN_V3_ID_MASK_TYPE


def can_v2_frame_module_id(arbitration_id: int) -> int:
    return (int(arbitration_id) & CAN_V3_ID_MASK_MODULE) >> 3


def can_v2_input_events_broadcast_id() -> int:
    return can_v2_frame_id(CAN_V2_CLASS_INPUT_EVENTS, CAN_V3_BROADCAST_MODULE_ID)


def can_v2_config_request_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONFIG_REQUEST, module_id)


def can_v2_config_request_id_for_command(target_module_id: int, command: int) -> int:
    """Arbitration ID CONFIG_REQUEST — broadcast 0x7F8 (shared protocol.pack)."""
    return _pack_config_req_id_for_command(target_module_id, command)

def can_v2_config_response_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONFIG_RESPONSE, module_id)


def can_v2_control_command_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONTROL_COMMAND, module_id)


# can_v2_control_command_id_for_pc_shutter imported from protocol.pack

def can_v2_ota_data_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_OTA_DATA, module_id)


def can_v2_ota_status_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_OTA_STATUS, module_id)


def can_v2_state_telemetry_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_STATE_TELEMETRY, module_id)


def can_v2_sensor_events_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_SENSOR_EVENTS, module_id)


# Frame types whose payloads are always plaintext on the wire (telemetry,
# control/bind events, OTA, button input). CONFIG_REQUEST/RESPONSE are
# excluded because they may carry encrypted (Secure TLV) config payloads.
PLAINTEXT_TELEMETRY_CLASSES = frozenset(
    {
        CAN_V2_CLASS_CONTROL_COMMAND,
        CAN_V2_CLASS_OTA_DATA,
        CAN_V2_CLASS_OTA_STATUS,
        CAN_V2_CLASS_INPUT_EVENTS,
        CAN_V2_CLASS_SENSOR_EVENTS,
        CAN_V2_CLASS_STATE_TELEMETRY,
    }
)


def is_plaintext_telemetry_id(arbitration_id: int) -> bool:
    return can_v2_frame_class(arbitration_id) in PLAINTEXT_TELEMETRY_CLASSES


UNKNOWN_MODULE_IDS = {0, 0xFF}


def can_id_to_bus(arbitration_id: int, *, legacy_11bit: bool = True) -> tuple[int, bool]:
    """V3 frames are always standard 11-bit (mask to 11 bits, never extended)."""
    return int(arbitration_id) & 0x7FF, False


def normalize_legacy_rx_id(
    arbitration_id: int, *, is_extended: bool = False, legacy_11bit: bool = True
) -> int:
    """V3 RX IDs are already standard 11-bit — mask and return as-is."""
    return int(arbitration_id) & 0x7FF


SECURE_TLV_TYPE_CONFIG_REQUEST = 1
SECURE_TLV_TYPE_CONFIG_RESPONSE = 2
SECURE_TLV_TYPE_CAN_FRAME = 3
SECURE_TLV_CHUNK_BYTES = 4
SECURE_TLV_MAC_BYTES = 8

SERVICE_MODE_TIMEOUT_S = 30
SERVICE_MODE_REFRESH_INTERVAL_S = 10.0
MODULE_RETENTION_SECONDS = 20.0
AUTO_SUMMARY_REQUEST_INTERVAL_S = 5.0
AUTO_MODULE_TREE_REFRESH_INTERVAL_S = 0.8

HW_TYPE_ESP32E = 1
HW_TYPE_XIAO_C6 = 2
HW_TYPE_WAVESHARE_C6_ZERO = 3
HW_TYPE_WEMOS_D1_MINI_ESP32 = 4
HW_TYPE_OTHER = 255

# Backward-compatible aliases used by older tests and helper scripts.
HW_TYPE_ESP32 = HW_TYPE_ESP32E
HW_TYPE_ESP32C6 = HW_TYPE_XIAO_C6

HW_TYPE_NAME_MAP = {
    HW_TYPE_ESP32E: "ESP32-WROOM-32E",
    HW_TYPE_XIAO_C6: "XIAO ESP32-C6",
    HW_TYPE_WAVESHARE_C6_ZERO: "Waveshare ESP32-C6 Zero",
    HW_TYPE_WEMOS_D1_MINI_ESP32: "Wemos D1 Mini ESP32",
    HW_TYPE_OTHER: "Inny",
}

HW_TYPE_TO_PINOUT = {
    HW_TYPE_ESP32E: "ESP32-WROOM-32E",
    HW_TYPE_XIAO_C6: "XIAO ESP32-C6",
    HW_TYPE_WAVESHARE_C6_ZERO: "Waveshare ESP32-C6 Zero",
    HW_TYPE_WEMOS_D1_MINI_ESP32: "Wemos D1 Mini ESP32",
}

PIN_ROLE_MAP = {
    "Unused": 0,
    "Button": 1,
    "Relay": 2,
    "DS18B20": 3,
    "BinarySensor": 4,
    "I2C_SDA": 5,
    "I2C_SCL": 6,
    "HC595": 7,
    "SHUTTER_UP": 8,
    "SHUTTER_DOWN": 9,
    "MCP23017": 10,
    "NTC": 11,
    "WS2812": 12,
}

NTC_RSERIES_CHOICES = {
    0: "10 kΩ",
    1: "4.7 kΩ",
    2: "22 kΩ",
    3: "47 kΩ",
}

SENSOR_TYPE_NTC = 5

GPIO_ASSIGNMENT_CHOICES = [
    "Unused",
    "Button",
    "Sensor binarny",
    "Relay",
    "NTC (termistor)",
    "1-Wire (DS18B20)",
    "I2C SDA",
    "I2C SCL",
    "74HC595 DATA",
    "74HC595 CLOCK",
    "74HC595 LATCH",
    "74HC595 OE",
    "MCP23017 RESET",
    "WS2812B (taśma LED)",
]

SHIFT595_RELAY_BASE_INDEX = 17
SHIFT595_MAX_REGISTERS = 5
SHIFT595_RELAY_COUNT_PER_REGISTER = 8
MCP23017_OUTPUT_COUNT = 16
MCP23017_RELAY_BASE_INDEX = SHIFT595_RELAY_BASE_INDEX + SHIFT595_MAX_REGISTERS * SHIFT595_RELAY_COUNT_PER_REGISTER  # = 57
MCP23017_RELAY_CAN_BASE = MCP23017_RELAY_BASE_INDEX  # alias (HA addon / parser)
MCP23017_BUTTON_BASE_INDEX = 9   # firmware: btn_num = MAX_BUTTONS(8) + pin_idx + 1

ACTION_MAP = {
    "Jednoklik": 1,
    "Dwuklik": 2,
    "Trojklik": 3,
    "Czteroklik": 4,
    "Piecioklik": 5,
    "Dlugie nacisniecie": 6,
}

STATE_MAP = {
    "Wylacz": 0,
    "Zalacz (permanentne)": 1,
    "Zalacz": 1,
    "Przelacz": 2,
}

STATE_LABEL_BY_CODE = {
    0: "Wylacz",
    1: "Zalacz (permanentne)",
    2: "Przelacz",
}

# BIND_* imported from protocol.pack







def unpack_relay_state_byte(packed: int, kind_byte: int = 0) -> tuple[int, int]:
    """Decode relay + state from GET route response byte (V3 packed wire)."""
    packed = int(packed) & 0xFF
    kind_byte = int(kind_byte) & 0xFF
    relay6 = packed & 0x3F
    state6 = (packed >> 6) & 0x03
    if state6 != 0:
        return relay6, state6
    if packed <= 63:
        return relay6, (kind_byte >> 4) & 0x03
    return packed, (kind_byte >> 4) & 0x03




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


def _rgb332_pack(r: int, g: int, b: int) -> int:
    r = int(r) & 0xFF
    g = int(g) & 0xFF
    b = int(b) & 0xFF
    return ((r & 0xE0) | ((g & 0xE0) >> 3) | ((b & 0xC0) >> 6)) & 0xFF


def _kelvin_to_byte(kelvin: int) -> int:
    k = max(2700, min(6500, int(kelvin)))
    return int((k - 2700) * 255 / (6500 - 2700)) & 0xFF


def pack_set_led_binding_args(
    source_module: int,
    button: int,
    action: int,
    effect_id: int,
    duration_s: int,
    r: int,
    g: int,
    b: int,
    strip_index: int = 1,
    *,
    strip_type: int = 0,
    kelvin: int | None = None,
) -> list[int]:
    meta = (int(strip_index) & 0x0F) | ((int(effect_id) & 0x07) << 4)
    if int(strip_type) == 1:
        color_byte = _kelvin_to_byte(kelvin if kelvin is not None else 2700)
    else:
        color_byte = _rgb332_pack(r, g, b)
    return [
        int(source_module) & 0xFF,
        int(button) & 0xFF,
        int(action) & 0xFF,
        meta,
        int(duration_s) & 0xFF,
        color_byte & 0xFF,
    ]


BINARY_EDGE_LABELS = {
    "Rosnace": 1,
    "Opadajace": 2,
    "Oba zbocza": 3,
    "Stan czujnika 1do1": 4,
}

BINARY_EDGE_TOF_OFF_DELAY = 5

BINARY_MAPPING_TRIGGER_LABELS = {
    **BINARY_EDGE_LABELS,
    "TOF (PIR)": BINARY_EDGE_TOF_OFF_DELAY,
}

_LEGACY_BINARY_TRIGGER_LABELS = {
    "Czujnik PIR TOF — ruch (narastajace)": 1,
    "Czujnik PIR TOF — koniec (opadajace)": 2,
    "Czujnik PIR TOF — oba zbocza": 3,
    "Czujnik PIR TOF — lustro 1:1": 4,
}


def binary_edge_mode_from_trigger_label(label: str) -> int | None:
    text = (label or "").strip()
    if text in BINARY_MAPPING_TRIGGER_LABELS:
        return int(BINARY_MAPPING_TRIGGER_LABELS[text])
    if text in _LEGACY_BINARY_TRIGGER_LABELS:
        return int(_LEGACY_BINARY_TRIGGER_LABELS[text])
    return None


SHUTTER_TRIGGER_LABELS = {
    "Otwieranie": (1, 1),
    "Zamykanie": (1, 2),
    "Stop": (1, 0),
}


# MCP23017 per-pin role

RELAY_LINK_TRIGGER_ON = 1
RELAY_LINK_TRIGGER_OFF = 2
RELAY_LINK_TRIGGER_ANY = 3
RELAY_LINK_TRIGGER_MIRROR = 4

RELAY_LINK_TRIGGER_NAME = {
    RELAY_LINK_TRIGGER_ON: "Włączenie (ON)",
    RELAY_LINK_TRIGGER_OFF: "Wyłączenie (OFF)",
    RELAY_LINK_TRIGGER_ANY: "Zmiana stanu",
    RELAY_LINK_TRIGGER_MIRROR: "Lustro (kopiuj stan)",
}


def unpack_get_relay_link_response(data: list[int] | tuple[int, ...]) -> tuple[int, int, int, int, int]:
    """Decode GET_RELAY_LINK (119) response fields data[3..7]."""
    if len(data) < 8:
        raise ValueError("GET_RELAY_LINK response too short")
    return (
        int(data[3]) & 0xFF,
        int(data[4]) & 0xFF,
        int(data[5]) & 0xFF,
        int(data[6]) & 0xFF,
        int(data[7]) & 0xFF,
    )


def unpack_get_led_binding_response(payload: list[int]) -> dict:
    """Decode GET_LED_BINDING (115) CONFIG_RESPONSE payload."""
    if len(payload) < 8:
        raise ValueError("GET_LED_BINDING response too short")
    status = int(payload[2]) & 0xFF
    strip_index = int(payload[3]) & 0x0F or 1
    source_module = int(payload[4]) & 0xFF
    button = int(payload[5]) & 0xFF
    action = int(payload[6]) & 0xFF
    meta = int(payload[7]) & 0xFF
    effect_id = meta & 0x07
    duration_s = (meta >> 3) & 0x1F
    return {
        "status": status,
        "strip_index": strip_index,
        "source_module": source_module,
        "button": button,
        "action": action,
        "effect_id": effect_id,
        "duration_s": duration_s,
    }


# OTA

# MCP23017 per-pin role constants (mcp23017_pin_roles[])
MCP23017_PIN_ROLE_UNUSED = 0
MCP23017_PIN_ROLE_RELAY  = 1
MCP23017_PIN_ROLE_BUTTON = 2
MCP23017_PIN_ROLE_SENSOR = 3

OTA_STATUS_READY    = 0
OTA_STATUS_PROGRESS = 1
OTA_STATUS_NACK     = 2
OTA_STATUS_DONE     = 3
OTA_STATUS_ERROR    = 4

OTA_PAYLOAD_BYTES = 5  # bytes of firmware per OTA_DATA frame
OTA_BATCH_FRAMES = 64


MAX_SHUTTERS = 28

V2_CTRL_SHUTTER_CMD = 1
SHUTTER_CMD_OPEN = 1
SHUTTER_CMD_CLOSE = 2
SHUTTER_CMD_STOP = 3
SHUTTER_CMD_SET_POSITION = 4



STATE_TELEMETRY_MAX_GPIO_NUM = 48

SHUTTER_TRAVEL_DS_MIN = 10
SHUTTER_TRAVEL_DS_MAX = 6000
SHUTTER_DIR_STOPPED = 0
SHUTTER_DIR_OPENING = 1
SHUTTER_DIR_CLOSING = 2

MODULE_NAME_MAX_LEN = 15
MODULE_NAME_CHUNK_READ = 3  # GET_MODULE_NAME response: do 3 znaków na offset
MODULE_NAME_PART_BYTES = 5  # znaków na ramkę CAN (arg0=part, arg1..5=dane)
MODULE_NAME_PART_COUNT = 3  # 3 * 5 = 15

LOG_MAX_LINES = 4000

CONFIG_STATUS_OK = 0
