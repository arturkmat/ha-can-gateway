"""CAN protocol constants shared by configurator and tools — V3 transport.

V3 wire layout: ``can_id = (module_id << 3) | frame_type`` as a standard 11-bit
frame (``is_extended_id=False``). All legacy fixed 29-bit / flat 0x1CE0xxxx IDs
have been removed; build arbitration IDs with the ``can_v2_*`` helpers below.
This mirrors ``konfigurator_windows_usb_can/protocol_constants.py`` and the
firmware ``can_protocol.h`` so every layer agrees.
"""

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


def can_v2_config_response_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONFIG_RESPONSE, module_id)


def can_v2_control_command_id(module_id: int) -> int:
    return can_v2_frame_id(CAN_V2_CLASS_CONTROL_COMMAND, module_id)


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
    "Zalacz": 1,
    "Przelacz": 2,
    "Impuls": 2,
}

STATE_LABEL_BY_CODE = {
    0: "Wylacz",
    1: "Zalacz",
    2: "Przelacz",
}

BIND_RELAY_STATE_USE_PULSE = 3
BIND_RELAY_STATE_TIMED_MIN = 128
BINDING_FLAG_TIMED = 0x01
BIND_FLAG_TIMED_SEC = BINDING_FLAG_TIMED
BINDING_FLAG_USE_RELAY_PULSE = 0x02
BIND_FLAG_USE_RELAY_PULSE = BINDING_FLAG_USE_RELAY_PULSE


def format_binding_state_label(code: int) -> str:
    code = int(code)
    if code == BIND_RELAY_STATE_USE_PULSE:
        return "Impuls przekaznika"
    if code >= BIND_RELAY_STATE_TIMED_MIN:
        return f"Czasowe {code - BIND_RELAY_STATE_TIMED_MIN} min"
    return STATE_LABEL_BY_CODE.get(code, str(code))


def parse_binding_state_label(state_label: str) -> tuple[int, int]:
    label = (state_label or "").strip()
    if label.lower().startswith("impuls przek"):
        return BIND_RELAY_STATE_USE_PULSE, 0
    if label.lower().startswith("czasowe"):
        parts = label.split()
        if len(parts) >= 2 and parts[1].isdigit():
            mins = int(parts[1])
            if mins < 1 or mins > 127:
                raise ValueError("Automat czasowy: 1..127 minut")
            return 1, mins
        raise ValueError(f"Nieprawidlowy format automatu czasowego: {state_label!r}")
    mapped = STATE_MAP.get(label)
    if mapped is None:
        raise ValueError(f"Nieznany stan mapowania: {state_label!r}")
    return int(mapped), 0


def pack_set_binding_args(
    source_module: int,
    button: int,
    action: int,
    relay: int,
    relay_state: int,
    *,
    timed_min: int = 0,
    use_relay_pulse: bool = False,
) -> list[int]:
    relay_state = int(relay_state)
    if relay_state == BIND_RELAY_STATE_USE_PULSE:
        use_relay_pulse = True
        relay_state = 1
    if relay_state > 2:
        raise ValueError("relay_state musi byc 0..2 (permanent), 3 (impuls) lub uzyj timed_min")
    args = [
        int(source_module) & 0xFF,
        int(button) & 0xFF,
        int(action) & 0xFF,
        int(relay) & 0xFF,
        relay_state,
    ]
    timed_min = int(timed_min)
    if timed_min > 0:
        if timed_min > 127:
            raise ValueError("Automat czasowy: maks. 127 minut")
        args.append(BINDING_FLAG_TIMED | ((timed_min & 0x7F) << 1))
    elif use_relay_pulse:
        args.append(BINDING_FLAG_USE_RELAY_PULSE)
    return args


def binding_state_label_to_wire(state_label: str, *, timed_min: int = 0) -> int:
    relay_state, mins = parse_binding_state_label(state_label)
    if timed_min > 0:
        mins = timed_min
    if mins > 0:
        return BIND_RELAY_STATE_TIMED_MIN + mins
    return relay_state


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
}

SHUTTER_TRIGGER_LABELS = {
    "Otwieranie": (1, 1),
    "Zamykanie": (1, 2),
    "Stop": (1, 0),
}

COMMAND_SET_MODULE_ID = 1
COMMAND_IDENTIFY = 2
COMMAND_GET_SUMMARY = 3
COMMAND_SET_MODULE_ID_BY_MAC = 4
COMMAND_SET_BINDING = 16
COMMAND_CLEAR_BINDINGS = 17
COMMAND_SET_MAPPING = 16
COMMAND_CLEAR_MAPPINGS = 17
COMMAND_SET_BUTTON_TIMING = 18
COMMAND_GET_BUTTON_TIMING = 19
COMMAND_GET_BINDING = 22
COMMAND_GET_BINDING_COUNT = 23
COMMAND_GET_BUILD_INFO = 24
COMMAND_SET_GPIO_ROLE = 32
COMMAND_CLEAR_GPIO_ROLE = 33
COMMAND_CLEAR_ALL_GPIO_ROLES = 34
COMMAND_GET_GPIO_ROLE = 35
COMMAND_SET_MODULE_NAME = 36
COMMAND_GET_MODULE_NAME = 37
COMMAND_GET_GPIO_VALUE = 38
COMMAND_SET_SERVICE_MODE = 39
COMMAND_SCAN_SENSORS = 40
COMMAND_SET_SHUTTER_TIME = 41
COMMAND_GET_SHUTTER_TIME = 42
COMMAND_SET_SHUTTER_TIME_OPEN  = 43
COMMAND_GET_SHUTTER_TIME_OPEN  = 44
COMMAND_SET_SHUTTER_TIME_CLOSE = 45
COMMAND_GET_SHUTTER_TIME_CLOSE = 46
COMMAND_SET_SHUTTER_MAPPING    = 47
COMMAND_CLEAR_SHUTTER_MAPPINGS        = 48
COMMAND_SET_SHUTTER_RELAYS            = 49
COMMAND_GET_SHUTTER_RELAYS            = 50
COMMAND_DELETE_BINDING                = 51
COMMAND_GET_SHUTTER_BINDING_COUNT     = 52
COMMAND_GET_SHUTTER_BINDING           = 53
COMMAND_SET_SHIFT595_FLAGS            = 54  # args: q_index, flags (bit0=NO, bit1=save_state)
COMMAND_GET_SHIFT595_FLAGS            = 55  # arg: q_index → resp: q_index, flags
COMMAND_SCAN_1WIRE                    = 56  # skanuje magistralę 1-Wire (DS18B20)
COMMAND_SCAN_I2C                      = 57  # skanuje magistralę I2C (SHT30, BME280)
COMMAND_REBOOT_MODULE                 = 58  # restart modułu (soft reset)
COMMAND_SET_RELAY_STATE               = 59  # args: relay_number(1..max), state(0=OFF,1=ON,2=TOGGLE)

# MCP23017 per-pin role
COMMAND_SET_MCP23017_PIN_ROLE         = 65  # args: pin_idx(0..15), role(0=unused,1=relay,2=button,3=sensor), flags
COMMAND_GET_MCP23017_PIN_ROLE         = 66  # arg: pin_idx → resp: pin_idx, role, flags
COMMAND_SCAN_MCP23017                 = 67  # skanuje I2C 0x20..0x27; resp: status, found_mask(1B)
COMMAND_GET_MCP23017_INPUT_STATE      = 68  # resp: status, gpa(1B), gpb(1B) — bieżący stan wejść
COMMAND_CAN_MUTE                      = 69  # args: enable(0|1), timeout_s — wycisza TX modulu (prócz 0x711/0x721)
COMMAND_SET_RELAY_PULSE              = 72  # args: relay_number(1..max), pulse_ms_lo, pulse_ms_hi; 0 = disabled
COMMAND_GET_RELAY_PULSE              = 73  # arg: relay_number -> resp: relay_number, pulse_ms_lo, pulse_ms_hi
COMMAND_PROVISION_SET_TARGET_MAC     = 74  # args: mac[6]
COMMAND_PROVISION_SET_CIPHERTEXT_PART = 75  # args: part_idx(0..3), b0,b1,b2,b3
COMMAND_PROVISION_APPLY              = 76  # applies/decrypts buffered provisioning key
COMMAND_PROVISION_GET_STATE          = 77  # resp: has_target_mac, parts_mask, has_key
COMMAND_PROVISION_SET_MASTER_KEY_PART = 78  # args: part_idx(0..7), b0,b1,b2,b3
COMMAND_PROVISION_APPLY_MASTER_KEY   = 79  # apply buffered 32-byte master key
COMMAND_PROVISION_GET_MASTER_KEY_STATE = 80  # resp: has_master_key, parts_mask
COMMAND_SET_BINDING_ROUTE            = 81  # args: button_number, action, target_module_id
COMMAND_CLEAR_BINDING_ROUTES         = 82  # clear local route table used for addressed button events
COMMAND_GET_BINDING_ROUTE_COUNT      = 83  # resp: count, max
COMMAND_GET_BINDING_ROUTE            = 84  # arg: index -> resp: button_number, action, target_module_id
COMMAND_SET_RELAY_BIND_ROUTE         = 85  # args: source_relay_number, target_module_id, target_relay_number
COMMAND_CLEAR_RELAY_BIND_ROUTES      = 86  # clear relay->relay addressed routes
COMMAND_GET_RELAY_BIND_ROUTE_COUNT   = 87  # resp: count, max
COMMAND_GET_RELAY_BIND_ROUTE         = 88  # arg: index -> resp: source_relay, target_module_id, target_relay
COMMAND_SET_BINARY_BIND_ROUTE        = 89  # args: source_sensor_idx, edge_mode, target_module_id, target_relay, target_state
COMMAND_CLEAR_BINARY_BIND_ROUTES     = 90  # clear binary sensor routes
COMMAND_GET_BINARY_BIND_ROUTE_COUNT  = 91  # resp: count, max
COMMAND_GET_BINARY_BIND_ROUTE        = 92  # arg: index -> resp: source_sensor_idx, edge_mode, target_module_id, target_relay, target_state
COMMAND_SET_SHUTTER_BIND_ROUTE       = 93  # args: source_shutter_idx, trigger_kind, trigger_value, target_module_id, target_relay
COMMAND_CLEAR_SHUTTER_BIND_ROUTES    = 94  # clear shutter source routes
COMMAND_GET_SHUTTER_BIND_ROUTE_COUNT = 95  # resp: count, max
COMMAND_GET_SHUTTER_BIND_ROUTE       = 96  # arg: index -> resp: source_shutter_idx, trigger_kind, trigger_value, target_module_id, target_relay
COMMAND_SET_SENSOR_BIND_ROUTE        = 97  # args: sensor_kind, sensor_idx, threshold_u8, target_module_id, target_relay
COMMAND_CLEAR_SENSOR_BIND_ROUTES     = 98  # clear sensor threshold routes
COMMAND_GET_SENSOR_BIND_ROUTE_COUNT  = 99  # resp: count, max
COMMAND_GET_SENSOR_BIND_ROUTE        = 100  # arg: index -> resp: sensor_kind, sensor_idx, threshold_u8, target_module_id, target_relay
COMMAND_SET_NTC_PARAMS               = 101  # args: sensor_idx, r25_lo, r25_hi, beta_lo, beta_hi
COMMAND_GET_NTC_PARAMS               = 102  # arg: sensor_idx -> resp: sensor_idx, r25_lo, r25_hi, beta_lo, beta_hi
COMMAND_SET_LED_STRIP_CONFIG         = 109  # wire: (type<<4)|idx, gpio, count_lo/hi, brightness, idle_effect
COMMAND_GET_LED_STRIP_CONFIG         = 110  # arg: strip_idx; resp: gpio|(type<<7), count, brightness, idle_effect, rgb332|kelvin
COMMAND_SET_LED_EFFECT               = 111  # new: strip_idx, effect_id, duration_s, r, gb_packed
COMMAND_SET_LED_BINDING              = 112  # new: strip_idx, src_mod, btn, action, effect_id, duration_s, r, gb_packed
COMMAND_CLEAR_LED_BINDINGS           = 113
COMMAND_GET_LED_BINDING_COUNT        = 114  # resp: count, max
COMMAND_GET_LED_BINDING              = 115  # arg: idx -> resp: src_mod, btn, action, effect_id, duration_s, r, gb_packed
COMMAND_SET_RELAY_LINK               = 116  # args: src_relay, trigger, target_module, target_relay, target_state
COMMAND_CLEAR_RELAY_LINKS            = 117
COMMAND_GET_RELAY_LINK_COUNT         = 118  # resp: count, max
COMMAND_GET_RELAY_LINK               = 119  # arg: index -> resp: src_relay, trigger, target_module, target_relay, target_state

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

COMMAND_GET_MCP23017_ROLE_DUMP        = 70  # args: chip_idx → resp: chip_idx, b0,b1,b2,b3 (16 pinów × 2 bity)
COMMAND_GET_SHIFT595_DUMP             = 71  # resp: register_count, data, clock, latch, oe

# OTA
COMMAND_OTA_BEGIN              = 60
COMMAND_OTA_SET_TIMESTAMP      = 61
COMMAND_OTA_END                = 62
COMMAND_OTA_ABORT              = 63
COMMAND_OTA_GET_INFO           = 64

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

# Najwyzszy fizyczny numer GPIO (ESP32-S3 = 48). W V3 wszystkie podramki telemetryczne
# dziela klase STATE_TELEMETRY; do odroznienia RELAY_GPIO_MAP od DEVICE_INFO sprawdzamy,
# czy bajty 2..7 to numery GPIO (<=48) lub 0xFF; realny MAC ma zwykle bajt > 48.
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
CONFIG_STATUS_UNSUPPORTED = 1
CONFIG_STATUS_INVALID_ARGUMENT = 2
CONFIG_STATUS_FULL = 3
CONFIG_STATUS_NOT_FOUND = 4

CONFIG_STATUS_LABELS = {
    CONFIG_STATUS_OK: "OK",
    CONFIG_STATUS_UNSUPPORTED: "UNSUPPORTED",
    CONFIG_STATUS_INVALID_ARGUMENT: "INVALID_ARGUMENT",
    CONFIG_STATUS_FULL: "FULL",
    CONFIG_STATUS_NOT_FOUND: "NOT_FOUND",
}
