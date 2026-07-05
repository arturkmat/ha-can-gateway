"""LED strip CONFIG payload helpers (commands 109–111) — mirrors konfigurator protocol_constants."""

from __future__ import annotations

LED_STRIP_GPIO_DISABLED = 0xFF
MAX_LED_STRIPS = 4
LED_STRIP_TYPE_RGB = 0
LED_STRIP_TYPE_CCT = 1
CCT_KELVIN_MIN = 2700
CCT_KELVIN_MAX = 6500

LED_EFFECT_OFF = 0
LED_EFFECT_SOLID = 1
LED_EFFECT_RAINBOW = 2
LED_EFFECT_CHASE = 3
LED_EFFECT_BREATHE = 4
LED_EFFECT_IDENTIFY = 5

LED_EFFECT_NAMES = {
    LED_EFFECT_OFF: "off",
    LED_EFFECT_SOLID: "solid",
    LED_EFFECT_RAINBOW: "rainbow",
    LED_EFFECT_CHASE: "chase",
    LED_EFFECT_BREATHE: "breathe",
    LED_EFFECT_IDENTIFY: "identify",
}

BUTTON_ACTION_NAMES = {
    1: "single",
    2: "double",
    3: "triple",
    4: "quad",
    5: "quint",
    6: "long",
}


def pack_led_strip_gb(g: int, b: int) -> int:
    g = int(g) & 0xFF
    b = int(b) & 0xFF
    return ((g & 0xF0) | ((b & 0xF0) >> 4)) & 0xFF


def rgb332_pack(r: int, g: int, b: int) -> int:
    r = int(r) & 0xFF
    g = int(g) & 0xFF
    b = int(b) & 0xFF
    return ((r & 0xE0) | ((g & 0xE0) >> 3) | ((b & 0xC0) >> 6)) & 0xFF


def rgb332_unpack(packed: int) -> tuple[int, int, int]:
    packed = int(packed) & 0xFF
    r = packed & 0xE0
    g = (packed & 0x1C) << 3
    b = (packed & 0x03) << 6
    r = r | (r >> 3) | (r >> 6)
    g = g | (g >> 3) | (g >> 6)
    b = b | (b >> 2) | (b >> 4) | (b >> 6)
    return r, g, b


def pack_strip_index_type(strip_index: int, strip_type: int = LED_STRIP_TYPE_RGB) -> int:
    return ((int(strip_type) & 0x0F) << 4) | (int(strip_index) & 0x0F)


def kelvin_to_byte(kelvin: int) -> int:
    k = max(CCT_KELVIN_MIN, min(CCT_KELVIN_MAX, int(kelvin)))
    return int((k - CCT_KELVIN_MIN) * 255 / (CCT_KELVIN_MAX - CCT_KELVIN_MIN)) & 0xFF


def kelvin_from_byte(value: int) -> int:
    v = int(value) & 0xFF
    return CCT_KELVIN_MIN + int(v * (CCT_KELVIN_MAX - CCT_KELVIN_MIN) / 255)


def cct_warm_cool_from_kelvin(kelvin: int) -> tuple[int, int]:
    k = max(CCT_KELVIN_MIN, min(CCT_KELVIN_MAX, int(kelvin)))
    warm = int(255 * (CCT_KELVIN_MAX - k) / (CCT_KELVIN_MAX - CCT_KELVIN_MIN))
    cool = 255 - warm
    return warm & 0xFF, cool & 0xFF


def unpack_led_binding_effect_duration(packed: int) -> tuple[int, int]:
    packed = int(packed) & 0xFF
    return packed & 0x07, (packed >> 3) & 0x1F


def pack_led_binding_meta(strip_index: int, effect_id: int) -> int:
    return (int(strip_index) & 0x0F) | ((int(effect_id) & 0x07) << 4)


def binding_color_byte_rgb(r: int, g: int, b: int) -> int:
    return rgb332_pack(r, g, b)


def binding_color_byte_cct_kelvin(kelvin: int) -> int:
    return kelvin_to_byte(kelvin)


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
    strip_type: int = LED_STRIP_TYPE_RGB,
    kelvin: int | None = None,
) -> list[int]:
    if strip_type == LED_STRIP_TYPE_CCT:
        color_byte = binding_color_byte_cct_kelvin(
            kelvin if kelvin is not None else CCT_KELVIN_MIN
        )
    else:
        color_byte = binding_color_byte_rgb(r, g, b)
    return [
        int(source_module) & 0xFF,
        int(button) & 0xFF,
        int(action) & 0xFF,
        pack_led_binding_meta(strip_index, effect_id),
        int(duration_s) & 0xFF,
        color_byte & 0xFF,
    ]


def unpack_get_led_binding_response(payload: list[int]) -> dict:
    if len(payload) < 8:
        raise ValueError("GET_LED_BINDING response too short")
    status = int(payload[2]) & 0xFF
    strip_index = int(payload[3]) & 0x0F or 1
    source_module = int(payload[4]) & 0xFF
    button = int(payload[5]) & 0xFF
    action = int(payload[6]) & 0xFF
    effect_id, duration_s = unpack_led_binding_effect_duration(int(payload[7]) & 0xFF)
    color_byte = int(payload[8]) & 0xFF if len(payload) >= 9 else 0
    return {
        "status": status,
        "strip_index": strip_index,
        "source_module": source_module,
        "button": button,
        "action": action,
        "effect_id": effect_id,
        "duration_s": duration_s,
        "color_byte": color_byte,
    }


def format_led_binding_summary(
    *,
    strip_index: int,
    source_module: int,
    button: int,
    action: int,
    effect_id: int,
    duration_s: int,
    strip_type: int = LED_STRIP_TYPE_RGB,
    color_byte: int = 0,
) -> str:
    effect = LED_EFFECT_NAMES.get(int(effect_id) & 0xFF, str(effect_id))
    act = BUTTON_ACTION_NAMES.get(int(action), str(action))
    dur = f" {int(duration_s)}s" if int(duration_s) else ""
    src = f"M{int(source_module)} btn{int(button)}"
    if int(strip_type) == LED_STRIP_TYPE_CCT:
        color = f" @ {kelvin_from_byte(color_byte)}K"
    elif color_byte:
        r, g, b = rgb332_unpack(color_byte)
        color = f" #{r:02X}{g:02X}{b:02X}"
    else:
        color = ""
    return f"{src} {act} → strip{int(strip_index)} {effect}{dur}{color}"


def pack_set_led_effect_args(
    effect_id: int,
    duration_s: int,
    r: int,
    g: int,
    b: int,
    strip_index: int = 1,
    strip_type: int = LED_STRIP_TYPE_RGB,
) -> list[int]:
    return [
        pack_strip_index_type(strip_index, strip_type),
        int(effect_id) & 0xFF,
        int(duration_s) & 0xFF,
        int(r) & 0xFF,
        pack_led_strip_gb(g, b),
    ]


def unpack_get_led_strip_config_response(payload: list[int]) -> dict:
    if len(payload) < 8:
        raise ValueError("GET_LED_STRIP_CONFIG response too short")
    status = int(payload[2]) & 0xFF
    gpio_raw = int(payload[3]) & 0xFF
    strip_type = (gpio_raw >> 7) & 0x01
    gpio = gpio_raw & 0x7F
    if len(payload) >= 9:
        count = (int(payload[4]) & 0xFF) | ((int(payload[5]) & 0xFF) << 8)
        brightness = int(payload[6]) & 0xFF
        idle_effect = int(payload[7]) & 0xFF
        color_packed = int(payload[8]) & 0xFF
    else:
        count = int(payload[4]) & 0xFF
        brightness = int(payload[5]) & 0xFF
        idle_effect = int(payload[6]) & 0xFF
        color_packed = int(payload[7]) & 0xFF
    kelvin: int | None = None
    if strip_type == LED_STRIP_TYPE_CCT:
        kelvin = kelvin_from_byte(color_packed)
        warm, cool = cct_warm_cool_from_kelvin(kelvin)
        r, g, b = warm, cool, 0
    else:
        r, g, b = rgb332_unpack(color_packed)
    return {
        "status": status,
        "gpio": gpio,
        "strip_type": strip_type,
        "count": count,
        "brightness": brightness,
        "idle_effect": idle_effect,
        "r": r,
        "g": g,
        "b": b,
        "kelvin": kelvin,
    }
