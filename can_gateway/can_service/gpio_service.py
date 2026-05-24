"""GPIO / pinout / pulse — odpowiednik zakładki GPIO w konfiguratorze Windows."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pinout_data import DEVICE_PINOUTS, STRAPPING_PIN_NOTES
from protocol_constants import (
    COMMAND_CLEAR_ALL_GPIO_ROLES,
    COMMAND_CLEAR_GPIO_ROLE,
    COMMAND_GET_GPIO_ROLE,
    COMMAND_GET_GPIO_VALUE,
    COMMAND_GET_RELAY_PULSE,
    COMMAND_OTA_GET_INFO,
    COMMAND_SET_BUTTON_TIMING,
    COMMAND_SET_GPIO_ROLE,
    COMMAND_SET_RELAY_PULSE,
    HW_TYPE_OTHER,
    HW_TYPE_TO_PINOUT,
    PIN_ROLE_MAP,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager

ROLE_NAME_BY_CODE = {v: k for k, v in PIN_ROLE_MAP.items()}
ROLE_CODE_BY_NAME = {k.lower(): v for k, v in PIN_ROLE_MAP.items()}


def pinout_name_for_hw_type(hw_type: int) -> str | None:
    return HW_TYPE_TO_PINOUT.get(int(hw_type))


def pinout_profile(hw_type: int) -> dict[str, Any] | None:
    name = pinout_name_for_hw_type(hw_type)
    if name is None:
        return None
    return DEVICE_PINOUTS.get(name)


def profile_gpios(profile: dict[str, Any]) -> list[int]:
    gpios: list[int] = []
    for key in ("left", "left_inner", "right_inner", "right"):
        for pin_entry in profile.get(key, []):
            if isinstance(pin_entry, dict):
                gpio = pin_entry.get("gpio")
                if gpio is not None and not pin_entry.get("reserved", False):
                    gpios.append(int(gpio))
            elif isinstance(pin_entry, int):
                gpios.append(int(pin_entry))
    return sorted(set(gpios))


def is_reserved_gpio(profile: dict[str, Any], gpio: int) -> bool:
    if gpio in (profile.get("can_tx"), profile.get("can_rx")):
        return True
    for key in ("left", "left_inner", "right_inner", "right"):
        for pin_entry in profile.get(key, []):
            if isinstance(pin_entry, dict) and pin_entry.get("gpio") == gpio:
                if pin_entry.get("reserved", False):
                    return True
    return False


def _parse_role(value: str | int) -> int | None:
    if isinstance(value, int):
        return value
    key = str(value).strip().lower().replace(" ", "_")
    if key in ROLE_CODE_BY_NAME:
        return ROLE_CODE_BY_NAME[key]
    try:
        code = int(key)
        if code in ROLE_NAME_BY_CODE:
            return code
    except ValueError:
        pass
    return None


def module_pinout_payload(bus: BusManager, module_id: int) -> dict[str, Any]:
    detail = bus.module_detail(module_id)
    if detail is None:
        return {"ok": False, "error": "module not found"}
    hw_type = int(detail.get("hw_type", HW_TYPE_OTHER))
    pinout_name = pinout_name_for_hw_type(hw_type)
    profile = pinout_profile(hw_type)
    if profile is None:
        return {
            "ok": True,
            "module_id": module_id,
            "hw_type": hw_type,
            "pinout_name": None,
            "profile": None,
            "strapping_notes": {},
            "gpios": [],
        }
    gpios = profile_gpios(profile)
    roles = detail.get("runtime", {}).get("gpio_roles") or {}
    values = detail.get("runtime", {}).get("gpio_values") or {}
    rows = []
    for gpio in gpios:
        role_row = roles.get(str(gpio), roles.get(gpio, {}))
        val_row = values.get(str(gpio), values.get(gpio, {}))
        rows.append(
            {
                "gpio": gpio,
                "reserved": is_reserved_gpio(profile, gpio),
                "strapping_note": STRAPPING_PIN_NOTES.get(pinout_name, {}).get(gpio),
                "role": role_row.get("role"),
                "role_name": role_row.get("role_name"),
                "index": role_row.get("index", 0),
                "flags": role_row.get("flags", 0),
                "logical": val_row.get("logical"),
                "raw": val_row.get("raw"),
            }
        )
    return {
        "ok": True,
        "module_id": module_id,
        "hw_type": hw_type,
        "pinout_name": pinout_name,
        "profile": profile,
        "strapping_notes": STRAPPING_PIN_NOTES.get(pinout_name, {}),
        "gpios": rows,
    }


def read_gpio_roles(bus: BusManager, module_id: int) -> dict[str, Any]:
    detail = bus.module_detail(module_id)
    if detail is None:
        return {"ok": False, "error": "module not found"}
    profile = pinout_profile(int(detail.get("hw_type", HW_TYPE_OTHER)))
    if profile is None:
        return {"ok": False, "error": "no pinout profile for hw_type"}

    roles: dict[str, dict[str, Any]] = {}
    for gpio in profile_gpios(profile):
        if is_reserved_gpio(profile, gpio):
            continue
        resp = bus.send_config_and_wait(module_id, COMMAND_GET_GPIO_ROLE, [gpio], timeout=0.35)
        if resp is None or len(resp) < 7 or int(resp[2]) != 0:
            roles[str(gpio)] = {
                "gpio": gpio,
                "role": PIN_ROLE_MAP["Unused"],
                "role_name": "Unused",
                "index": 0,
                "flags": 0,
            }
            continue
        role = int(resp[3])
        roles[str(gpio)] = {
            "gpio": gpio,
            "role": role,
            "role_name": ROLE_NAME_BY_CODE.get(role, f"role_{role}"),
            "index": int(resp[4]),
            "flags": int(resp[5]),
        }
        time.sleep(0.04)
    return {"ok": True, "module_id": module_id, "roles": roles}


def read_gpio_values(bus: BusManager, module_id: int) -> dict[str, Any]:
    detail = bus.module_detail(module_id)
    if detail is None:
        return {"ok": False, "error": "module not found"}
    profile = pinout_profile(int(detail.get("hw_type", HW_TYPE_OTHER)))
    if profile is None:
        return {"ok": False, "error": "no pinout profile for hw_type"}

    runtime = detail.get("runtime", {})
    roles = runtime.get("gpio_roles") or {}
    values: dict[str, dict[str, Any]] = {}
    for gpio in profile_gpios(profile):
        if is_reserved_gpio(profile, gpio):
            continue
        role_info = roles.get(str(gpio), {})
        if role_info.get("role", PIN_ROLE_MAP["Unused"]) == PIN_ROLE_MAP["Unused"]:
            continue
        resp = bus.send_config_and_wait(module_id, COMMAND_GET_GPIO_VALUE, [gpio], timeout=0.2)
        if resp is None or len(resp) < 7 or int(resp[2]) != 0 or int(resp[6]) != 1:
            continue
        values[str(gpio)] = {
            "gpio": gpio,
            "logical": int(resp[3]),
            "raw": int(resp[4]),
            "role": int(resp[5]),
        }
        relay_index = int(role_info.get("index", 0))
        if int(role_info.get("role", PIN_ROLE_MAP["Unused"])) == PIN_ROLE_MAP["Relay"] and relay_index > 0:
            bus.store_relay_state(module_id, relay_index, bool(int(resp[3])))
        time.sleep(0.03)

    bus.store_gpio_values(module_id, values)
    return {"ok": True, "module_id": module_id, "values": values}


def set_gpio_role(
    bus: BusManager,
    module_id: int,
    gpio: int,
    *,
    role: str | int,
    index: int = 0,
    flags: int = 0,
) -> dict[str, Any]:
    role_code = _parse_role(role)
    if role_code is None:
        return {"ok": False, "error": "invalid role"}
    resp = bus.send_config_and_wait(
        module_id,
        COMMAND_SET_GPIO_ROLE,
        [int(gpio), role_code, int(index), int(flags)],
        timeout=0.6,
    )
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return {"ok": False, "error": "set_gpio_role failed"}
    verify = bus.send_config_and_wait(module_id, COMMAND_GET_GPIO_ROLE, [int(gpio)], timeout=0.35)
    if verify is None or len(verify) < 7 or int(verify[2]) != 0:
        return {"ok": True, "module_id": module_id, "gpio": gpio, "verified": False}
    bus.update_gpio_role(
        module_id,
        int(gpio),
        {
            "gpio": int(gpio),
            "role": int(verify[3]),
            "role_name": ROLE_NAME_BY_CODE.get(int(verify[3]), f"role_{verify[3]}"),
            "index": int(verify[4]),
            "flags": int(verify[5]),
        },
    )
    return {
        "ok": True,
        "module_id": module_id,
        "gpio": gpio,
        "verified": True,
        "role": int(verify[3]),
        "index": int(verify[4]),
        "flags": int(verify[5]),
    }


def clear_all_gpio_roles(bus: BusManager, module_id: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_CLEAR_ALL_GPIO_ROLES, timeout=1.0)
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    if ok:
        bus.clear_gpio_roles(module_id)
    return {"ok": ok, "module_id": module_id}


def set_relay_pulse_ms(bus: BusManager, module_id: int, relay_no: int, pulse_ms: int) -> dict[str, Any]:
    pulse = max(0, min(65535, int(pulse_ms)))
    lo = pulse & 0xFF
    hi = (pulse >> 8) & 0xFF
    resp = bus.send_config_and_wait(
        module_id,
        COMMAND_SET_RELAY_PULSE,
        [int(relay_no), lo, hi],
        timeout=0.5,
    )
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    if ok:
        bus.store_relay_pulse(module_id, int(relay_no), pulse)
    return {"ok": ok, "module_id": module_id, "relay_no": relay_no, "pulse_ms": pulse}


def read_relay_pulse_ms(bus: BusManager, module_id: int, relay_no: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_GET_RELAY_PULSE, [int(relay_no)], timeout=0.4)
    if resp is None or len(resp) < 6 or int(resp[2]) != 0:
        return {"ok": False, "error": "read failed"}
    pulse = int(resp[4]) | (int(resp[5]) << 8)
    bus.store_relay_pulse(module_id, int(relay_no), pulse)
    return {"ok": True, "module_id": module_id, "relay_no": relay_no, "pulse_ms": pulse}


def get_ota_info(bus: BusManager, module_id: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_OTA_GET_INFO, timeout=0.6)
    if resp is None or len(resp) < 4:
        return {"ok": False, "error": "no response"}
    status = int(resp[2])
    if status != 0:
        return {"ok": False, "error": f"status={status}"}
    ota_state = int(resp[3])
    epoch = None
    if len(resp) >= 8:
        epoch = (
            int(resp[4]) | (int(resp[5]) << 8) | (int(resp[6]) << 16) | (int(resp[7]) << 24)
        )
    return {
        "ok": True,
        "module_id": module_id,
        "ota_state": ota_state,
        "ota_epoch": epoch,
    }


def list_pinout_profiles() -> dict[str, Any]:
    return {"profiles": sorted(DEVICE_PINOUTS.keys())}


def set_button_timing(
    bus: BusManager,
    module_id: int,
    multiclick_ms: int,
    longpress_ms: int,
) -> dict[str, Any]:
    mc = max(100, min(2000, int(multiclick_ms)))
    lp = max(300, min(2550, int(longpress_ms)))
    resp = bus.send_config_and_wait(
        module_id,
        COMMAND_SET_BUTTON_TIMING,
        [mc // 10, lp // 10],
        timeout=0.6,
    )
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return {"ok": False, "error": "set button timing failed"}
    bus.store_button_timing(module_id, mc, lp)
    return {"ok": True, "module_id": int(module_id), "multiclick_ms": mc, "longpress_ms": lp}


def clear_gpio_role(bus: BusManager, module_id: int, gpio: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_CLEAR_GPIO_ROLE, [int(gpio)], timeout=0.5)
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    if ok:
        detail = bus.module_detail(int(module_id)) or {}
        roles = dict((detail.get("runtime") or {}).get("gpio_roles") or {})
        roles.pop(str(int(gpio)), None)
        bus.store_gpio_roles(int(module_id), roles)
    return {"ok": ok, "module_id": int(module_id), "gpio": int(gpio)}
