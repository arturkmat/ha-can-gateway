"""Build Home Assistant entity snapshots from CAN Gateway module state.

The add-on is the source of truth: integration ``can_gateway_v3`` (add-on mode)
consumes the persisted catalog from ``GET /api/entities`` — only entities
assigned in firmware (GPIO roles, relay/shutter maps, sensor scan), never
hypothetical slots from summary counts alone.
"""

from __future__ import annotations

from typing import Any

from protocol_constants import (
    MCP23017_RELAY_CAN_BASE,
    PIN_ROLE_MAP,
    SHIFT595_RELAY_BASE_INDEX,
    SHIFT595_MAX_REGISTERS,
    SHIFT595_RELAY_COUNT_PER_REGISTER,
)

MAX_LOCAL_RELAYS = 16
MCP23017_RELAY_ENTITY_BASE = 101

SENSOR_TYPE_NAMES = {
    1: "ds18b20",
    2: "i2c",
    3: "sht30",
    4: "bme280",
    5: "ntc",
    6: "binary",
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes", "high", "pressed", "active"}:
            return True
        if normalized in {"0", "false", "off", "no", "low", "released", "inactive", "unknown", "none", "null", ""}:
            return False
    return bool(value) if value is not None else default


def _int_dict(raw: Any) -> dict[int, int]:
    out: dict[int, int] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        try:
            out[int(key)] = int(value)
        except (TypeError, ValueError):
            pass
    return out


def _shutter_map(raw: Any) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    if not isinstance(raw, dict):
        return out
    for key, pair in raw.items():
        try:
            sid = int(key)
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                out[sid] = (int(pair[0]), int(pair[1]))
        except (TypeError, ValueError):
            pass
    return out


def _gpio_roles(raw: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for gpio_key, info in raw.items():
        if not isinstance(info, dict):
            continue
        try:
            gpio = int(info.get("gpio", gpio_key))
        except (TypeError, ValueError):
            continue
        out[gpio] = info
    return out


def _mcp_pins(raw: Any) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    if not isinstance(raw, dict):
        return out
    for chip, pins in raw.items():
        try:
            chip_i = int(chip)
        except (TypeError, ValueError):
            continue
        if isinstance(pins, (list, tuple, set)):
            out[chip_i] = {int(p) for p in pins}
    return out


def _mcp_pin_roles(raw: Any) -> dict[int, dict[int, int]]:
    out: dict[int, dict[int, int]] = {}
    if not isinstance(raw, dict):
        return out
    for chip_key, pin_map in raw.items():
        if not isinstance(pin_map, dict):
            continue
        try:
            chip_i = int(chip_key)
        except (TypeError, ValueError):
            continue
        roles: dict[int, int] = {}
        for pin_key, role in pin_map.items():
            try:
                roles[int(pin_key)] = int(role)
            except (TypeError, ValueError):
                continue
        if roles:
            out[chip_i] = roles
    return out


def _relay_state_map(rows: list[Any]) -> dict[int, bool]:
    out: dict[int, bool] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rn = row.get("relay_no")
        if rn is None:
            continue
        out[normalize_relay_no(int(rn))] = bool(row.get("on"))
    return out


def normalize_relay_no(relay_no: int) -> int:
    rn = int(relay_no)
    if rn <= 0:
        return rn
    mcp_span = 16 * 8
    if MCP23017_RELAY_ENTITY_BASE <= rn < (MCP23017_RELAY_ENTITY_BASE + mcp_span):
        return MCP23017_RELAY_CAN_BASE + (rn - MCP23017_RELAY_ENTITY_BASE)
    return rn


def hc595_register_count(hw_flags: int | None) -> int:
    if hw_flags is None:
        return 0
    return (int(hw_flags) >> 4) & 0x07


def hc595_relay_numbers(hw_flags: int | None) -> range:
    regs = hc595_register_count(hw_flags)
    if regs <= 0:
        return range(0)
    first = SHIFT595_RELAY_BASE_INDEX
    last = SHIFT595_RELAY_BASE_INDEX + regs * SHIFT595_RELAY_COUNT_PER_REGISTER
    return range(first, last)


def switch_uid(module_id: int, relay_no: int) -> str | None:
    r = normalize_relay_no(int(relay_no))
    if r <= 0:
        return None
    if r <= MAX_LOCAL_RELAYS:
        return f"m{module_id}_local_relay{r}"
    if r < MCP23017_RELAY_CAN_BASE:
        return f"m{module_id}_hc595_relay{r}"
    chip_off = (r - MCP23017_RELAY_CAN_BASE) // 16
    return f"m{module_id}_mcp_chip{chip_off}_relay{r}"


def relay_display_name(module_id: int, relay_no: int, *, pulse: bool = False) -> str:
    r = normalize_relay_no(int(relay_no))
    suffix = " Pulse" if pulse else ""
    if r <= MAX_LOCAL_RELAYS:
        return f"CAN M{module_id} Relay {r}{suffix}"
    if r < MCP23017_RELAY_CAN_BASE:
        return f"CAN M{module_id} HC595 Relay {r}{suffix}"
    return f"CAN M{module_id} MCP Relay {r}{suffix}"


def _entity(
    *,
    platform: str,
    unique_id: str,
    name: str,
    module_id: int,
    value: Any = None,
    attributes: dict[str, Any] | None = None,
    device_class: str | None = None,
    unit: str | None = None,
    icon: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "platform": platform,
        "unique_id": unique_id,
        "name": name,
        "module_id": int(module_id),
        "value": value,
        "attributes": dict(attributes or {}),
    }
    if device_class:
        row["device_class"] = device_class
    if unit:
        row["unit"] = unit
    if icon:
        row["icon"] = icon
    return row


def _decode_sensor_telemetry(data: list[int], sensor_type: int) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if len(data) < 4:
        return payload
    if sensor_type in (1, 5):
        payload["temperature_c"] = int.from_bytes(bytes(data[:4]), byteorder="little", signed=True) / 100.0
    elif sensor_type in (2, 3) and len(data) >= 4:
        payload["temperature_c"] = int.from_bytes(bytes(data[:2]), byteorder="little", signed=True) / 100.0
        payload["humidity_pct"] = int.from_bytes(bytes(data[2:4]), byteorder="little", signed=False) / 100.0
    elif sensor_type == 4 and len(data) >= 4:
        payload["pressure_pa"] = int.from_bytes(bytes(data[:4]), byteorder="little", signed=False)
    return payload


def _sensor_entities_from_scan(module_id: int, scan: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    flags = int(scan.get("flags", 0))
    ds18_field = int(scan.get("ds18_gpio_or_count", 0))
    ds_count = (ds18_field >> 6) & 0x03

    def _temp(sensor_no: int, sensor_type: str, label: str = "temperature") -> None:
        uid = f"m{module_id}_s{sensor_no}_{sensor_type}_{label}"
        out.append(
            _entity(
                platform="sensor",
                unique_id=uid,
                name=f"CAN M{module_id} {sensor_type} {sensor_no} {label.replace('_', ' ').title()}",
                module_id=module_id,
                value=None,
                attributes={"module_id": module_id, "sensor_no": sensor_no, "sensor_type": sensor_type},
                device_class="temperature" if label == "temperature" else label,
                unit="°C" if label == "temperature" else "%" if label == "humidity" else "Pa",
            )
        )

    if (flags & 0x01) and ds_count > 0:
        for sensor_no in range(1, ds_count + 1):
            _temp(sensor_no, "ds18b20")
    if flags & 0x02:
        _temp(1, "sht30")
        uid = f"m{module_id}_s1_sht30_humidity"
        out.append(
            _entity(
                platform="sensor",
                unique_id=uid,
                name=f"CAN M{module_id} sht30 1 Humidity",
                module_id=module_id,
                value=None,
                attributes={"module_id": module_id, "sensor_no": 1, "sensor_type": "sht30"},
                device_class="humidity",
                unit="%",
            )
        )
    if flags & 0x04:
        _temp(1, "bme280")
        out.append(
            _entity(
                platform="sensor",
                unique_id=f"m{module_id}_s1_bme280_humidity",
                name=f"CAN M{module_id} bme280 1 Humidity",
                module_id=module_id,
                value=None,
                attributes={"module_id": module_id, "sensor_no": 1, "sensor_type": "bme280"},
                device_class="humidity",
                unit="%",
            )
        )
        out.append(
            _entity(
                platform="sensor",
                unique_id=f"m{module_id}_s1_bme280_pressure",
                name=f"CAN M{module_id} bme280 1 Pressure",
                module_id=module_id,
                value=None,
                attributes={"module_id": module_id, "sensor_no": 1, "sensor_type": "bme280"},
                device_class="pressure",
                unit="Pa",
            )
        )
    if flags & 0x08:
        _temp(1, "ntc")
    return out


def _sensor_entities_from_telemetry(module_id: int, sensors: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    latest: dict[tuple[int, str, str], Any] = {}
    for row in sensors:
        if not isinstance(row, dict):
            continue
        sensor_no = int(row.get("sensor_no", 0))
        sensor_type_code = int(row.get("sensor_type", 0))
        sensor_type = SENSOR_TYPE_NAMES.get(sensor_type_code, f"type{sensor_type_code}")
        data = row.get("data")
        if not isinstance(data, list):
            continue
        decoded = _decode_sensor_telemetry([int(b) & 0xFF for b in data], sensor_type_code)
        prefix = f"m{module_id}_s{sensor_no}_{sensor_type}"
        common = {"module_id": module_id, "sensor_no": sensor_no, "sensor_type": sensor_type}
        if "temperature_c" in decoded:
            latest[(sensor_no, sensor_type, "temperature")] = (f"{prefix}_temperature", decoded["temperature_c"], common)
        if "humidity_pct" in decoded:
            latest[(sensor_no, sensor_type, "humidity")] = (
                f"{prefix}_humidity",
                decoded["humidity_pct"],
                common,
            )
        if "pressure_pa" in decoded:
            latest[(sensor_no, sensor_type, "pressure")] = (
                f"{prefix}_pressure",
                decoded["pressure_pa"],
                common,
            )
    for (_no, stype, label), (uid, value, attrs) in latest.items():
        device_class = label
        unit = "°C" if label == "temperature" else "%" if label == "humidity" else "Pa"
        out.append(
            _entity(
                platform="sensor",
                unique_id=uid,
                name=f"CAN M{module_id} {stype} {attrs['sensor_no']} {label.title()}",
                module_id=module_id,
                value=value,
                attributes=attrs,
                device_class=device_class,
                unit=unit,
            )
        )
    return out


def _is_relay_gpio_role(info: dict[str, Any], relay_role: int | None) -> bool:
    role_code = info.get("role")
    role_name = str(info.get("role_name") or "")
    return role_name == "Relay" or role_code == relay_role


def _assigned_relay_numbers(
    *,
    relay_gpio_map: dict[int, int],
    gpio_roles: dict[int, dict[str, Any]],
    relay_pulse_ms: dict[int, int],
    mcp_pins: dict[int, set[int]],
    mcp_pin_roles: dict[int, dict[int, int]],
    hw_flags: int,
    shutter_reserved: set[int],
    relay_bind_routes: dict[int, dict[str, Any]] | None = None,
) -> tuple[set[int], dict[int, str]]:
    """Relay indices assigned in firmware — never from summary counts or passive telemetry."""
    relay_role = PIN_ROLE_MAP.get("Relay")
    nums: set[int] = set()
    has_roles = bool(gpio_roles)

    for info in gpio_roles.values():
        if not _is_relay_gpio_role(info, relay_role):
            continue
        idx = int(info.get("index", 0))
        if idx > 0:
            nums.add(idx)

    for rn, gpio in relay_gpio_map.items():
        rn_i = int(rn)
        g = int(gpio)
        if rn_i <= 0 or g == 255:
            continue
        if has_roles:
            ginfo = gpio_roles.get(g)
            if ginfo is None or not _is_relay_gpio_role(ginfo, relay_role):
                continue
        nums.add(rn_i)

    for rn, pulse in relay_pulse_ms.items():
        if int(pulse) > 0:
            nums.add(int(rn))

    regs = hc595_register_count(hw_flags)
    if regs > 0:
        nums.update(hc595_relay_numbers(hw_flags))

    relay_pins = dict(mcp_pins)
    for chip_off, roles in mcp_pin_roles.items():
        relay_pins.setdefault(chip_off, set()).update(
            pin for pin, role in roles.items() if int(role) == 1
        )
    for chip_off, pins in relay_pins.items():
        for local_pin in pins:
            nums.add(MCP23017_RELAY_CAN_BASE + int(chip_off) * 16 + int(local_pin))

    nums.difference_update(shutter_reserved)
    
    # Dodaj relaye z bindingu czasowego
    relay_binding_type = {}
    if relay_bind_routes:
        for route_idx, route in relay_bind_routes.items():
            relay_no = route.get("relay")
            relay_state = route.get("relay_state", 0)
            if relay_no:
                nums.add(relay_no)
                # Określ typ: 0-2 = permanentny, >=128 = czasowy (legacy) lub flaga BIND_FLAG_TIMED_SEC
                binding_type = "timed" if relay_state >= 128 else "permanent"
                relay_binding_type[relay_no] = binding_type
    
    return nums, relay_binding_type


def build_entities_for_module(mod: dict[str, Any]) -> list[dict[str, Any]]:
    """Return HA entity rows for one module export dict (from export_module_dict)."""
    module_id = mod.get("module_id")
    if not isinstance(module_id, int) or not (1 <= module_id <= 254):
        return []

    rt = mod.get("runtime") if isinstance(mod.get("runtime"), dict) else {}
    entities: list[dict[str, Any]] = []

    entities.append(
        _entity(
            platform="binary_sensor",
            unique_id=f"m{module_id}_online",
            name=f"CAN M{module_id} Online",
            module_id=module_id,
            value=True,
            attributes={"module_id": module_id, "presence": "seen_bus_traffic"},
            device_class="connectivity",
            icon="mdi:lan-connect",
        )
    )

    relay_gpio_map = _int_dict(rt.get("relay_gpio_map"))
    relay_pulse_ms = _int_dict(rt.get("relay_pulse_ms"))
    shutter_map = _shutter_map(rt.get("shutter_map"))
    mcp_pins = _mcp_pins(rt.get("mcp_relay_pins"))
    mcp_pin_roles = _mcp_pin_roles(rt.get("mcp_pin_roles"))
    hw_flags = int(rt.get("hw_flags", mod.get("hw_flags") or 0))
    gpio_roles = _gpio_roles(rt.get("gpio_roles"))
    led_strips = rt.get("led_strips") if isinstance(rt.get("led_strips"), dict) else {}

    shutter_reserved: set[int] = set()
    for ro, rc in shutter_map.values():
        if ro > 0:
            shutter_reserved.add(ro)
        if rc > 0:
            shutter_reserved.add(rc)

    relay_states = _relay_state_map(rt.get("relays") or [])
    relay_nos, relay_binding_type = _assigned_relay_numbers(
        relay_gpio_map=relay_gpio_map,
        gpio_roles=gpio_roles,
        relay_pulse_ms=relay_pulse_ms,
        mcp_pins=mcp_pins,
        mcp_pin_roles=mcp_pin_roles,
        hw_flags=hw_flags,
        shutter_reserved=shutter_reserved,
        relay_bind_routes=rt.get("relay_bind_routes", {}),
    )

    for relay_no in sorted(relay_nos):
        if relay_no in shutter_reserved:
            continue
        pulse = int(relay_pulse_ms.get(relay_no, 0))
        uid = switch_uid(module_id, relay_no)
        if uid is None:
            continue
        is_on = relay_states.get(relay_no, False)
        source = "mcp23017" if relay_no >= MCP23017_RELAY_CAN_BASE else ("hc595" if relay_no > MAX_LOCAL_RELAYS else "local")
        chip_offset = None
        local_pin = None
        if source == "mcp23017":
            chip_offset = (relay_no - MCP23017_RELAY_CAN_BASE) // 16
            local_pin = (relay_no - MCP23017_RELAY_CAN_BASE) % 16
        attrs = {
            "module_id": module_id,
            "relay_no": relay_no,
            "relay_entity_no": relay_no,
            "gpio_no": relay_gpio_map.get(relay_no) if relay_no <= MAX_LOCAL_RELAYS else local_pin,
            "source": source,
            "source_stream": source,
            "chip_offset": chip_offset,
            "local_pin": local_pin,
            "pulse_ms": pulse,
        }
        # Dodaj binding_type dla timed relay
        binding_type = relay_binding_type.get(relay_no, "permanent")
        attrs["binding_type"] = binding_type
        
        if pulse > 0:
            entities.append(
                _entity(
                    platform="button",
                    unique_id=f"{uid}_pulse",
                    name=relay_display_name(module_id, relay_no, pulse=True),
                    module_id=module_id,
                    value=None,
                    attributes=attrs,
                    icon="mdi:flash",
                )
            )
        else:
            entities.append(
                _entity(
                    platform="switch",
                    unique_id=uid,
                    name=relay_display_name(module_id, relay_no),
                    module_id=module_id,
                    value=bool(is_on),
                    attributes=attrs,
                    icon="mdi:light-switch",
                )
            )

    for shutter_no, (relay_open, relay_close) in sorted(shutter_map.items()):
        if relay_open <= 0 and relay_close <= 0:
            continue
        uid = f"m{module_id}_shutter{shutter_no}"
        shutter_row = next(
            (s for s in (rt.get("shutters") or []) if isinstance(s, dict) and int(s.get("shutter_no", -1)) == shutter_no),
            None,
        )
        position = shutter_row.get("position") if isinstance(shutter_row, dict) else None
        direction = shutter_row.get("direction") if isinstance(shutter_row, dict) else 0
        entities.append(
            _entity(
                platform="cover",
                unique_id=uid,
                name=f"CAN M{module_id} Shutter {shutter_no}",
                module_id=module_id,
                value={
                    "position": position,
                    "direction": direction,
                    "direction_text": (shutter_row or {}).get("direction_text", "stopped"),
                },
                attributes={
                    "module_id": module_id,
                    "shutter_no": shutter_no,
                    "relay_open_no": relay_open,
                    "relay_close_no": relay_close,
                    "gpio_open_no": relay_gpio_map.get(relay_open) if relay_open > 0 else None,
                    "gpio_close_no": relay_gpio_map.get(relay_close) if relay_close > 0 else None,
                    "gpio_no": None,
                },
                device_class="shutter",
            )
        )

    button_role = PIN_ROLE_MAP.get("Button")
    button_nos: set[int] = set()
    for gpio, info in gpio_roles.items():
        role_code = info.get("role")
        role_name = str(info.get("role_name") or "")
        if role_name != "Button" and role_code != button_role:
            continue
        button_no = int(info.get("index", 0))
        if button_no <= 0:
            continue
        button_nos.add(button_no)
        uid = f"m{module_id}_btn{button_no}_action"
        entities.append(
            _entity(
                platform="sensor",
                unique_id=uid,
                name=f"CAN M{module_id} Button {button_no} Action",
                module_id=module_id,
                value=None,
                attributes={
                    "module_id": module_id,
                    "button_no": button_no,
                    "action_code": None,
                    "gpio_no": gpio,
                },
                icon="mdi:gesture-tap-button",
            )
        )

    mcp_input_state = rt.get("mcp_input_state") if isinstance(rt.get("mcp_input_state"), dict) else {}

    def _mcp_pin_value(chip_off: int, local_pin: int) -> bool | None:
        state = mcp_input_state.get(str(chip_off)) or mcp_input_state.get("0")
        if not isinstance(state, dict):
            return None
        register = int(state.get("gpa", 0)) if int(local_pin) < 8 else int(state.get("gpb", 0))
        bit = int(local_pin) if int(local_pin) < 8 else int(local_pin) - 8
        return bool(register & (1 << bit))

    for chip_off, roles in sorted(mcp_pin_roles.items()):
        for local_pin, role_code in sorted(roles.items()):
            role_code = int(role_code)
            if role_code == 1:
                continue
            if role_code == 2:
                uid = f"m{module_id}_mcp_chip{chip_off}_pin{local_pin}_button"
                entities.append(
                    _entity(
                        platform="sensor",
                        unique_id=uid,
                        name=f"CAN M{module_id} MCP{chip_off} A{local_pin} Button",
                        module_id=module_id,
                        value=_mcp_pin_value(chip_off, local_pin),
                        attributes={
                            "module_id": module_id,
                            "chip_offset": chip_off,
                            "local_pin": local_pin,
                            "role": role_code,
                            "source": "mcp23017",
                        },
                        icon="mdi:gesture-tap-button",
                    )
                )
            elif role_code == 3:
                uid = f"m{module_id}_mcp_chip{chip_off}_pin{local_pin}_binary"
                entities.append(
                    _entity(
                        platform="binary_sensor",
                        unique_id=uid,
                        name=f"CAN M{module_id} MCP{chip_off} Pin {local_pin}",
                        module_id=module_id,
                        value=_mcp_pin_value(chip_off, local_pin),
                        attributes={
                            "module_id": module_id,
                            "chip_offset": chip_off,
                            "local_pin": local_pin,
                            "role": role_code,
                            "source": "mcp23017",
                        },
                    )
                )

    ws2812_role = PIN_ROLE_MAP.get("WS2812")
    strip_indices: set[int] = set()
    for gpio, info in gpio_roles.items():
        role_code = info.get("role")
        role_name = str(info.get("role_name") or "")
        if role_name != "WS2812" and role_code != ws2812_role:
            continue
        strip_index = int(info.get("index", 0))
        if strip_index <= 0 or strip_index in strip_indices:
            continue
        strip_indices.add(strip_index)
        strip_cfg = led_strips.get(str(strip_index)) or led_strips.get(strip_index) or {}
        strip_type = int(strip_cfg.get("strip_type", 0)) if isinstance(strip_cfg, dict) else 0
        type_label = "CCT" if strip_type == 2 else "RGB"
        uid = f"m{module_id}_led_strip{strip_index}"
        entities.append(
            _entity(
                platform="light",
                unique_id=uid,
                name=f"CAN M{module_id} LED Strip {strip_index} ({type_label})",
                module_id=module_id,
                value={
                    "is_on": bool(strip_cfg.get("is_on")) if isinstance(strip_cfg, dict) else False,
                    "brightness": int(strip_cfg.get("brightness", 128)) if isinstance(strip_cfg, dict) else 128,
                },
                attributes={
                    "module_id": module_id,
                    "strip_index": strip_index,
                    "gpio": gpio,
                    "strip_type": strip_type,
                },
                icon="mdi:led-strip-variant",
            )
        )
    for strip_key, strip_cfg in led_strips.items():
        if not isinstance(strip_cfg, dict):
            continue
        try:
            strip_index = int(strip_key)
        except (TypeError, ValueError):
            continue
        if strip_index in strip_indices:
            continue
        gpio = int(strip_cfg.get("gpio", 0))
        if gpio <= 0:
            continue
        strip_indices.add(strip_index)
        strip_type = int(strip_cfg.get("strip_type", 0))
        type_label = "CCT" if strip_type == 2 else "RGB"
        uid = f"m{module_id}_led_strip{strip_index}"
        entities.append(
            _entity(
                platform="light",
                unique_id=uid,
                name=f"CAN M{module_id} LED Strip {strip_index} ({type_label})",
                module_id=module_id,
                value={
                    "is_on": bool(strip_cfg.get("is_on", False)),
                    "brightness": int(strip_cfg.get("brightness", 128)),
                },
                attributes={
                    "module_id": module_id,
                    "strip_index": strip_index,
                    "gpio": gpio,
                    "strip_type": strip_type,
                    "pixel_count": strip_cfg.get("count"),
                },
                icon="mdi:led-strip-variant",
            )
        )

    gpio_values = rt.get("gpio_values") or {}
    for gpio_key, info in gpio_values.items():
        if not isinstance(info, dict):
            continue
        if not _coerce_bool(info.get("valid", True), default=True):
            continue
        gpio = int(info.get("gpio", gpio_key))
        role_name = str(info.get("role_name") or "")
        role_code = info.get("role")
        is_binary = role_name in ("BinarySensor", "Button") or role_code == PIN_ROLE_MAP.get("BinarySensor")
        if not is_binary and role_name != "Button":
            continue
        uid = f"m{module_id}_gpio{gpio}_binary"
        entities.append(
            _entity(
                platform="binary_sensor",
                unique_id=uid,
                name=f"CAN M{module_id} GPIO {gpio}",
                module_id=module_id,
                value=_coerce_bool(info.get("logical", 0)),
                attributes={
                    "module_id": module_id,
                    "gpio": gpio,
                    "raw": info.get("raw"),
                    "role": role_code,
                    "index": info.get("index"),
                },
            )
        )

    sensor_scan = rt.get("sensor_scan")
    if isinstance(sensor_scan, dict):
        entities.extend(_sensor_entities_from_scan(module_id, sensor_scan))
    entities.extend(_sensor_entities_from_telemetry(module_id, rt.get("sensors") or []))

    # De-duplicate by unique_id (telemetry wins over scan templates)
    by_uid: dict[str, dict[str, Any]] = {}
    for ent in entities:
        by_uid[ent["unique_id"]] = ent
    return list(by_uid.values())


def build_entities_snapshot(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mod in modules:
        if not isinstance(mod, dict):
            continue
        for ent in build_entities_for_module(mod):
            uid = ent["unique_id"]
            if uid in seen:
                continue
            seen.add(uid)
            out.append(ent)
    return out
