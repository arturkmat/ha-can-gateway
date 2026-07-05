"""Build Home Assistant entity snapshots from CAN Gateway module state.

The add-on is the source of truth: integration can_gateway_v2 consumes
``entities`` from ``/api/state`` or ``/api/entities`` without re-deriving
relay/shutter/sensor logic from raw CAN.
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
    hw_flags = int(rt.get("hw_flags", mod.get("hw_flags") or 0))
    button_count = mod.get("button_count")
    relay_count = mod.get("relay_count")
    shutter_count = mod.get("shutter_count")

    shutter_reserved: set[int] = set()
    for ro, rc in shutter_map.values():
        if ro > 0:
            shutter_reserved.add(ro)
        if rc > 0:
            shutter_reserved.add(rc)

    relay_states = _relay_state_map(rt.get("relays") or [])
    control_relays = mod.get("control_relays")
    if isinstance(control_relays, list):
        for row in control_relays:
            if isinstance(row, dict) and row.get("relay_no") is not None:
                relay_states[normalize_relay_no(int(row["relay_no"]))] = bool(row.get("on"))

    relay_nos: set[int] = set(relay_gpio_map.keys())
    for _gpio, info in (rt.get("gpio_roles") or {}).items():
        if not isinstance(info, dict):
            continue
        if info.get("role_name") == "Relay" or info.get("role") == PIN_ROLE_MAP.get("Relay"):
            idx = int(info.get("index", 0))
            if idx > 0:
                relay_nos.add(idx)
    relay_nos.update(relay_states.keys())
    relay_nos.update(relay_pulse_ms.keys())
    for relay_no in hc595_relay_numbers(hw_flags):
        relay_nos.add(int(relay_no))
    for chip_off, pins in mcp_pins.items():
        for local_pin in pins:
            relay_nos.add(MCP23017_RELAY_CAN_BASE + int(chip_off) * 16 + int(local_pin))
    if not relay_nos and isinstance(relay_count, int) and relay_count > 0:
        hc595_count = hc595_register_count(hw_flags) * SHIFT595_RELAY_COUNT_PER_REGISTER
        mcp_count = sum(len(p) for p in mcp_pins.values())
        local_slots = max(0, relay_count - hc595_count - mcp_count)
        relay_nos.update(range(1, min(MAX_LOCAL_RELAYS, local_slots) + 1))

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

    if not shutter_map and isinstance(shutter_count, int) and shutter_count > 0:
        for shutter_no in range(1, min(28, shutter_count) + 1):
            uid = f"m{module_id}_shutter{shutter_no}"
            entities.append(
                _entity(
                    platform="cover",
                    unique_id=uid,
                    name=f"CAN M{module_id} Shutter {shutter_no}",
                    module_id=module_id,
                    value={"position": None, "direction": 0, "direction_text": "stopped"},
                    attributes={
                        "module_id": module_id,
                        "shutter_no": shutter_no,
                        "relay_open_no": None,
                        "relay_close_no": None,
                        "gpio_open_no": None,
                        "gpio_close_no": None,
                        "gpio_no": None,
                    },
                    device_class="shutter",
                )
            )

    if isinstance(button_count, int) and button_count > 0:
        for button_no in range(1, min(64, button_count) + 1):
            uid = f"m{module_id}_btn{button_no}_action"
            entities.append(
                _entity(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} Button {button_no} Action",
                    module_id=module_id,
                    value=None,
                    attributes={"module_id": module_id, "button_no": button_no, "action_code": None, "gpio_no": None},
                    icon="mdi:gesture-tap-button",
                )
            )

    gpio_values = rt.get("gpio_values") or {}
    for gpio_key, info in gpio_values.items():
        if not isinstance(info, dict):
            continue
        if not info.get("valid", True):
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
                value=bool(info.get("logical", 0)),
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
