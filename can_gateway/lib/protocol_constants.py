"""CAN protocol constants shared by configurator and tools."""

CAN_ID_EXT_BASE = 0x1CE00000
CAN_ID_CONFIG_REQUEST = CAN_ID_EXT_BASE | 0x710
CAN_ID_CONFIG_RESPONSE = CAN_ID_EXT_BASE | 0x711
CAN_ID_DEVICE_INFO = CAN_ID_EXT_BASE | 0x701
CAN_ID_GPIO_VALUES = CAN_ID_EXT_BASE | 0x520
CAN_ID_RELAYS = CAN_ID_EXT_BASE | 0x600
CAN_ID_RELAYS_MCP23017 = CAN_ID_EXT_BASE | 0x602
CAN_ID_SHUTTER_CMD = CAN_ID_EXT_BASE | 0x650
CAN_ID_SHUTTER_STATUS = CAN_ID_EXT_BASE | 0x660
CAN_ID_SENSORS = CAN_ID_EXT_BASE | 0x400
CAN_ID_OTA_DATA = CAN_ID_EXT_BASE | 0x720
CAN_ID_OTA_STATUS = CAN_ID_EXT_BASE | 0x721
CAN_ID_SECURE_TLV_REQUEST = CAN_ID_EXT_BASE | 0x730
CAN_ID_SECURE_TLV_RESPONSE = CAN_ID_EXT_BASE | 0x731
CAN_ID_BUTTONS = CAN_ID_EXT_BASE | 0x100
CAN_ID_BUTTON_BIND_EVENT = CAN_ID_EXT_BASE | 0x101
CAN_ID_RELAY_BIND_EVENT = CAN_ID_EXT_BASE | 0x102
CAN_ID_BINARY_BIND_EVENT = CAN_ID_EXT_BASE | 0x103
CAN_ID_SHUTTER_BIND_EVENT = CAN_ID_EXT_BASE | 0x104
CAN_ID_SENSOR_BIND_EVENT = CAN_ID_EXT_BASE | 0x105

# Ramki telemetryczne — zawsze plaintext na magistrali (modul bez klucza).
# Modul z MASTER_KEY nadaje te same ID przez Secure TLV CAN_FRAME (0x731).
PLAINTEXT_TELEMETRY_CAN_IDS = frozenset(
    {
        CAN_ID_GPIO_VALUES,
        CAN_ID_RELAYS,
        CAN_ID_RELAYS_MCP23017,
        CAN_ID_SHUTTER_CMD,
        CAN_ID_SHUTTER_STATUS,
        CAN_ID_SENSORS,
        CAN_ID_OTA_DATA,
        CAN_ID_OTA_STATUS,
        CAN_ID_BUTTONS,
        CAN_ID_BUTTON_BIND_EVENT,
        CAN_ID_RELAY_BIND_EVENT,
        CAN_ID_BINARY_BIND_EVENT,
        CAN_ID_SHUTTER_BIND_EVENT,
        CAN_ID_SENSOR_BIND_EVENT,
    }
)

UNKNOWN_MODULE_IDS = {0, 0xFF}


def can_id_to_bus(arbitration_id: int, *, legacy_11bit: bool) -> tuple[int, bool]:
    """Map internal extended CAN_ID to bus arbitration_id + is_extended_id."""
    if legacy_11bit:
        return arbitration_id & 0x7FF, False
    return arbitration_id, True


def normalize_legacy_rx_id(arbitration_id: int, *, is_extended: bool, legacy_11bit: bool) -> int:
    """Normalize received ID to internal extended form (0x1CE00000 | legacy)."""
    if legacy_11bit and not is_extended:
        return CAN_ID_EXT_BASE | (arbitration_id & 0x7FF)
    return arbitration_id

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
SHUTTER_TRAVEL_DS_MIN = 10
SHUTTER_TRAVEL_DS_MAX = 6000
SHUTTER_DIR_STOPPED = 0
SHUTTER_DIR_OPENING = 1
SHUTTER_DIR_CLOSING = 2

MODULE_NAME_MAX_LEN = 15
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
