"""Zapis mapowań — jak send_mappings / clear_mappings w konfiguratorze Windows."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from protocol_constants import (
    ACTION_MAP,
    COMMAND_CLEAR_BINARY_BIND_ROUTES,
    COMMAND_CLEAR_BINDING_ROUTES,
    COMMAND_CLEAR_MAPPINGS,
    COMMAND_CLEAR_RELAY_BIND_ROUTES,
    COMMAND_CLEAR_SENSOR_BIND_ROUTES,
    COMMAND_CLEAR_SHUTTER_BIND_ROUTES,
    COMMAND_CLEAR_SHUTTER_MAPPINGS,
    COMMAND_SET_BINDING_ROUTE,
    COMMAND_SET_BINARY_BIND_ROUTE,
    COMMAND_SET_MAPPING,
    COMMAND_SET_RELAY_BIND_ROUTE,
    COMMAND_SET_SENSOR_BIND_ROUTE,
    COMMAND_SET_SHUTTER_BIND_ROUTE,
    COMMAND_SET_SHUTTER_MAPPING,
    STATE_MAP,
    UNKNOWN_MODULE_IDS,
)
from .mapping_service import read_all_mappings

if TYPE_CHECKING:
    from .bus_manager import BusManager

_STATE_NAME = {code: name for name, code in STATE_MAP.items()}
_ACTION_NAME = {code: name for name, code in ACTION_MAP.items()}


def _action_code(action: str | int) -> int:
    if isinstance(action, int):
        return int(action)
    return int(ACTION_MAP.get(str(action), 0))


def _relay_state(state: str | int) -> int:
    if isinstance(state, int):
        return int(state)
    key = str(state).strip()
    if key in STATE_MAP:
        return int(STATE_MAP[key])
    low = key.lower()
    if low in ("on", "zalacz", "włącz"):
        return 1
    if low in ("off", "wylacz", "wyłącz"):
        return 0
    if low == "toggle":
        return 2
    return 1


def apply_button_relay_mapping(
    bus: BusManager,
    *,
    source_module_id: int,
    target_module_id: int,
    button_num: int,
    action_code: int,
    relay_num: int,
    relay_state: int,
) -> bool:
    src = int(source_module_id)
    tgt = int(target_module_id)
    resp = bus.send_config_and_wait(
        tgt,
        COMMAND_SET_MAPPING,
        [src, int(button_num), int(action_code), int(relay_num), int(relay_state)],
        timeout=1.0,
    )
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return False
    if src != tgt:
        rr = bus.send_config_and_wait(
            src,
            COMMAND_SET_BINDING_ROUTE,
            [int(button_num), int(action_code), tgt],
            timeout=1.0,
        )
        if rr is None or len(rr) < 3 or int(rr[2]) != 0:
            return False
    return True


def apply_button_shutter_mapping(
    bus: BusManager,
    *,
    source_module_id: int,
    target_module_id: int,
    button_num: int,
    action_code: int,
    shutter_num: int,
    shutter_cmd: int,
) -> bool:
    src = int(source_module_id)
    tgt = int(target_module_id)
    resp = bus.send_config_and_wait(
        tgt,
        COMMAND_SET_SHUTTER_MAPPING,
        [src, int(button_num), int(action_code), int(shutter_num), int(shutter_cmd)],
        timeout=1.0,
    )
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return False
    if src != tgt:
        rr = bus.send_config_and_wait(
            src,
            COMMAND_SET_BINDING_ROUTE,
            [int(button_num), int(action_code), tgt],
            timeout=1.0,
        )
        if rr is None or len(rr) < 3 or int(rr[2]) != 0:
            return False
    return True


def send_mappings(bus: BusManager, module_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    mid = int(module_id)
    if mid in UNKNOWN_MODULE_IDS:
        return {"ok": False, "error": "invalid module_id"}

    has_relay_links = any(str(r.get("kind", r.get("target_type", ""))).lower() in ("relay_link", "link relay") for r in rows)
    needs_routes = any(
        str(r.get("receiver", r.get("receiver_type", "Lokalny"))) != "Lokalny"
        or int(r.get("target_module_id", mid)) != mid
        for r in rows
        if str(r.get("kind", "button_relay")) in ("button_relay", "button_shutter", "relay")
    )
    if has_relay_links:
        bus.send_config_and_wait(mid, COMMAND_CLEAR_RELAY_BIND_ROUTES, timeout=0.5)
    if needs_routes:
        bus.send_config_and_wait(mid, COMMAND_CLEAR_BINDING_ROUTES, timeout=0.5)

    applied = 0
    errors: list[str] = []
    for row in rows:
        kind = str(row.get("kind", "")).lower()
        if not kind:
            tt = str(row.get("target_type", "Przekaznik"))
            kind = "button_shutter" if tt == "Roleta" else "button_relay"

        try:
            if kind in ("relay_link", "link relay", "relay link"):
                src_relay = int(row.get("source_relay", row.get("relay_num", 0)))
                tgt_mod = int(row.get("target_module_id", mid))
                tgt_relay = int(row.get("target_relay", row.get("relay_num_target", 0)))
                resp = bus.send_config_and_wait(
                    mid,
                    COMMAND_SET_RELAY_BIND_ROUTE,
                    [src_relay, tgt_mod, tgt_relay],
                    timeout=0.8,
                )
                if resp and len(resp) >= 3 and int(resp[2]) == 0:
                    applied += 1
                else:
                    errors.append(f"relay_link R{src_relay}")
                continue

            if kind == "binary_route":
                resp = bus.send_config_and_wait(
                    mid,
                    COMMAND_SET_BINARY_BIND_ROUTE,
                    [
                        int(row["source_sensor"]),
                        int(row.get("edge_mode", 3)),
                        int(row["target_module_id"]),
                        int(row["target_relay"]),
                        int(row.get("state", 2)),
                    ],
                    timeout=0.8,
                )
                if resp and len(resp) >= 3 and int(resp[2]) == 0:
                    applied += 1
                else:
                    errors.append("binary_route")
                continue

            if kind == "sensor_route":
                resp = bus.send_config_and_wait(
                    mid,
                    COMMAND_SET_SENSOR_BIND_ROUTE,
                    [
                        int(row.get("sensor_kind", 1)),
                        int(row.get("sensor_index", 1)),
                        int(row.get("threshold", 25)) & 0xFF,
                        int(row["target_module_id"]),
                        int(row["target_relay"]),
                    ],
                    timeout=0.8,
                )
                if resp and len(resp) >= 3 and int(resp[2]) == 0:
                    applied += 1
                else:
                    errors.append("sensor_route")
                continue

            src = int(row.get("source_module_id", mid))
            tgt = int(row.get("target_module_id", mid))
            btn = int(row.get("button_num", row.get("button", 1)))
            act = _action_code(row.get("action_code", row.get("action", 1)))

            if kind in ("button_shutter", "shutter"):
                shutter = int(row.get("shutter_num", row.get("shutter", 1)))
                scmd = int(row.get("shutter_cmd", row.get("shutter_command", 1)))
                if apply_button_shutter_mapping(
                    bus,
                    source_module_id=src,
                    target_module_id=tgt,
                    button_num=btn,
                    action_code=act,
                    shutter_num=shutter,
                    shutter_cmd=scmd,
                ):
                    applied += 1
                else:
                    errors.append(f"shutter btn{btn}")
                continue

            relay = int(row.get("relay_num", row.get("relay", 1)))
            st = _relay_state(row.get("relay_state", row.get("state", 1)))
            if apply_button_relay_mapping(
                bus,
                source_module_id=src,
                target_module_id=tgt,
                button_num=btn,
                action_code=act,
                relay_num=relay,
                relay_state=st,
            ):
                applied += 1
            else:
                errors.append(f"relay btn{btn}->R{relay}")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))

    snapshot = read_all_mappings(bus, mid)
    return {
        "ok": applied > 0 and not errors,
        "module_id": mid,
        "applied": applied,
        "errors": errors,
        "mappings": snapshot.get("mappings", []),
    }


def clear_mappings(bus: BusManager, module_id: int) -> dict[str, Any]:
    mid = int(module_id)
    if mid in UNKNOWN_MODULE_IDS:
        return {"ok": False, "error": "invalid module_id"}
    bus.send_config_and_wait(mid, COMMAND_CLEAR_MAPPINGS, timeout=1.0)
    bus.send_config_and_wait(mid, COMMAND_CLEAR_SHUTTER_MAPPINGS, timeout=0.5)
    bus.send_config_and_wait(mid, COMMAND_CLEAR_RELAY_BIND_ROUTES, timeout=0.5)
    bus.send_config_and_wait(mid, COMMAND_CLEAR_BINARY_BIND_ROUTES, timeout=0.5)
    bus.send_config_and_wait(mid, COMMAND_CLEAR_SHUTTER_BIND_ROUTES, timeout=0.5)
    bus.send_config_and_wait(mid, COMMAND_CLEAR_SENSOR_BIND_ROUTES, timeout=0.5)
    time.sleep(0.1)
    bus.store_mappings(mid, [])
    return {"ok": True, "module_id": mid}
