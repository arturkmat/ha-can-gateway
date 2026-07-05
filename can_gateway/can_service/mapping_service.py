"""Odczyt mapowań z modułu CAN — jak zakładka Mapowanie w konfiguratorze Windows."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from protocol_constants import (
    ACTION_MAP,
    BINARY_EDGE_LABELS,
    COMMAND_GET_BINDING,
    COMMAND_GET_BINDING_COUNT,
    COMMAND_GET_BINARY_BIND_ROUTE,
    COMMAND_GET_BINARY_BIND_ROUTE_COUNT,
    COMMAND_GET_LED_BINDING,
    COMMAND_GET_LED_BINDING_COUNT,
    COMMAND_GET_RELAY_BIND_ROUTE,
    COMMAND_GET_RELAY_BIND_ROUTE_COUNT,
    COMMAND_GET_RELAY_LINK,
    COMMAND_GET_RELAY_LINK_COUNT,
    COMMAND_GET_SENSOR_BIND_ROUTE,
    COMMAND_GET_SENSOR_BIND_ROUTE_COUNT,
    COMMAND_GET_SHUTTER_BIND_ROUTE,
    COMMAND_GET_SHUTTER_BIND_ROUTE_COUNT,
    COMMAND_GET_SHUTTER_BINDING,
    COMMAND_GET_SHUTTER_BINDING_COUNT,
    RELAY_LINK_TRIGGER_MIRROR,
    RELAY_LINK_TRIGGER_NAME,
    SHUTTER_TRIGGER_LABELS,
    STATE_LABEL_BY_CODE,
    UNKNOWN_MODULE_IDS,
    format_binding_state_label,
    unpack_get_led_binding_response,
    unpack_get_relay_link_response,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager

_SHUTTER_CMD_LABEL = {1: "Otworz", 2: "Zamknij", 3: "Stop"}
_ACTION_NAME = {code: name for name, code in ACTION_MAP.items()}
_EDGE_NAME = {code: name for name, code in BINARY_EDGE_LABELS.items()}
_SENSOR_KIND = {1: "DS18B20", 2: "BME280 Temp", 3: "SHT30 Temp", 5: "NTC"}


def _read_bindings(bus: BusManager, module_id: int) -> list[tuple[int, int, int, int, int]] | None:
    count_resp = bus.send_config_and_wait(module_id, COMMAND_GET_BINDING_COUNT, timeout=0.5)
    if count_resp is None or len(count_resp) < 4 or int(count_resp[2]) != 0:
        return None
    total = int(count_resp[3])
    items: list[tuple[int, int, int, int, int]] = []
    for idx in range(min(total, 64)):
        resp = bus.send_config_and_wait(module_id, COMMAND_GET_BINDING, [idx], timeout=0.4)
        if resp is None or len(resp) < 8:
            break
        status = int(resp[2])
        if status == 4 or status != 0:
            break
        items.append((int(resp[3]), int(resp[4]), int(resp[5]), int(resp[6]), int(resp[7])))
        time.sleep(0.005)
    return items


def _read_shutter_bindings(bus: BusManager, module_id: int) -> list[tuple[int, int, int, int, int]]:
    count_resp = bus.send_config_and_wait(module_id, COMMAND_GET_SHUTTER_BINDING_COUNT, timeout=0.5)
    if count_resp is None or len(count_resp) < 4 or int(count_resp[2]) != 0:
        return []
    total = int(count_resp[3])
    items: list[tuple[int, int, int, int, int]] = []
    for idx in range(min(total, 64)):
        resp = bus.send_config_and_wait(module_id, COMMAND_GET_SHUTTER_BINDING, [idx], timeout=0.4)
        if resp is None or len(resp) < 8:
            break
        status = int(resp[2])
        if status == 4 or status != 0:
            break
        items.append((int(resp[3]), int(resp[4]), int(resp[5]), int(resp[6]), int(resp[7])))
        time.sleep(0.005)
    return items


def _read_relay_links(bus: BusManager, module_id: int) -> list[tuple[int, int, int, int, int]]:
    count_resp = bus.send_config_and_wait(module_id, COMMAND_GET_RELAY_LINK_COUNT, timeout=0.5)
    if count_resp is None or len(count_resp) < 5 or int(count_resp[2]) != 0:
        return []
    total = int(count_resp[3])
    items: list[tuple[int, int, int, int, int]] = []
    for idx in range(min(total, 16)):
        resp = bus.send_config_and_wait(module_id, COMMAND_GET_RELAY_LINK, [idx], timeout=0.4)
        if resp is None or len(resp) < 8 or int(resp[2]) != 0:
            break
        try:
            items.append(unpack_get_relay_link_response(resp))
        except ValueError:
            break
        time.sleep(0.005)
    return items


def _read_led_bindings(bus: BusManager, module_id: int) -> list[dict[str, int]]:
    count_resp = bus.send_config_and_wait(module_id, COMMAND_GET_LED_BINDING_COUNT, timeout=0.5)
    if count_resp is None or len(count_resp) < 5 or int(count_resp[2]) != 0:
        return []
    total = int(count_resp[3])
    items: list[dict[str, int]] = []
    for idx in range(min(total, 64)):
        resp = bus.send_config_and_wait(module_id, COMMAND_GET_LED_BINDING, [idx], timeout=0.4)
        if resp is None or len(resp) < 8 or int(resp[2]) != 0:
            break
        try:
            data = unpack_get_led_binding_response(resp)
        except ValueError:
            break
        items.append(data)
        time.sleep(0.005)
    return items


def _row(
    *,
    button: str,
    action: str,
    receiver: str,
    target_id: str,
    target_type: str,
    target: str,
    state: str,
) -> dict[str, Any]:
    return {
        "button": button,
        "action": action,
        "receiver": receiver,
        "target_id": target_id,
        "target_type": target_type,
        "target": target,
        "state": state,
    }


def read_all_mappings(bus: BusManager, module_id: int) -> dict[str, Any]:
    mid = int(module_id)
    if mid in UNKNOWN_MODULE_IDS:
        return {"ok": False, "error": "invalid module_id"}

    local = _read_bindings(bus, mid)
    if local is None:
        return {
            "ok": False,
            "error": "Modul nie odpowiada na GET_BINDING_COUNT (stary firmware?)",
        }

    rows: list[dict[str, Any]] = []
    for src, btn, act, rly, st in local:
        rows.append(
            _row(
                button=f"Btn {btn}" if src == mid else f"M{src} Btn {btn}",
                action=_ACTION_NAME.get(act, f"akcja {act}"),
                receiver="Lokalny",
                target_id="-",
                target_type="Przekaznik",
                target=str(rly),
                state=STATE_LABEL_BY_CODE.get(st, str(st)),
            )
        )

    for src, btn, act, sht_num, sht_cmd in _read_shutter_bindings(bus, mid):
        rows.append(
            _row(
                button=f"Btn {btn}" if src == mid else f"M{src} Btn {btn}",
                action=_ACTION_NAME.get(act, f"akcja {act}"),
                receiver="Lokalny",
                target_id="-",
                target_type="Roleta",
                target=str(sht_num),
                state=_SHUTTER_CMD_LABEL.get(sht_cmd, str(sht_cmd)),
            )
        )

    other_ids = [
        int(m["module_id"])
        for m in bus.list_modules()
        if int(m.get("module_id", 0)) not in UNKNOWN_MODULE_IDS and int(m["module_id"]) != mid
    ]
    for oid in other_ids:
        for src, btn, act, rly, st in _read_bindings(bus, oid) or []:
            if src == mid:
                rows.append(
                    _row(
                        button=f"M{src} Btn {btn}",
                        action=_ACTION_NAME.get(act, f"akcja {act}"),
                        receiver="Zdalny",
                        target_id=str(oid),
                        target_type="Przekaznik",
                        target=str(rly),
                        state=STATE_LABEL_BY_CODE.get(st, str(st)),
                    )
                )
        for src, btn, act, sht_num, sht_cmd in _read_shutter_bindings(bus, oid):
            if src == mid:
                rows.append(
                    _row(
                        button=f"M{src} Btn {btn}",
                        action=_ACTION_NAME.get(act, f"akcja {act}"),
                        receiver="Zdalny",
                        target_id=str(oid),
                        target_type="Roleta",
                        target=str(sht_num),
                        state=_SHUTTER_CMD_LABEL.get(sht_cmd, str(sht_cmd)),
                    )
                )

    rr_count = bus.send_config_and_wait(mid, COMMAND_GET_RELAY_BIND_ROUTE_COUNT, timeout=0.4)
    if rr_count is not None and len(rr_count) >= 5 and int(rr_count[2]) == 0:
        for idx in range(int(rr_count[3])):
            rr = bus.send_config_and_wait(mid, COMMAND_GET_RELAY_BIND_ROUTE, [idx], timeout=0.35)
            if rr is None or len(rr) < 8 or int(rr[2]) != 0:
                continue
            rows.append(
                _row(
                    button=f"Btn {int(rr[3])}",
                    action=_ACTION_NAME.get(int(rr[4]), f"akcja {int(rr[4])}"),
                    receiver="Zdalny",
                    target_id=str(int(rr[5])),
                    target_type="Relay bind route",
                    target=str(int(rr[6])),
                    state=format_binding_state_label(int(rr[7])),
                )
            )

    for src_relay, trigger, target_mod, target_rly, target_state in _read_relay_links(bus, mid):
        trigger_label = RELAY_LINK_TRIGGER_NAME.get(trigger, str(trigger))
        state_label = (
            "-"
            if trigger == RELAY_LINK_TRIGGER_MIRROR
            else format_binding_state_label(target_state)
        )
        rows.append(
            _row(
                button=f"Relay {src_relay}",
                action=trigger_label,
                receiver="Lokalny" if target_mod == mid else "Zdalny",
                target_id=str(target_mod) if target_mod != mid else "-",
                target_type="Relay-link",
                target=str(target_rly),
                state=state_label,
            )
        )

    for binding in _read_led_bindings(bus, mid):
        rows.append(
            _row(
                button=(
                    f"M{binding['source_module']} Btn {binding['button']}"
                    if binding["source_module"] != mid
                    else f"Btn {binding['button']}"
                ),
                action=_ACTION_NAME.get(binding["action"], f"akcja {binding['action']}"),
                receiver="Lokalny" if binding["source_module"] == mid else "Zdalny",
                target_id=str(mid) if binding["source_module"] != mid else "-",
                target_type="Taśma LED",
                target=f"Taśma {binding['strip_index']}",
                state=f"Efekt {binding['effect_id']} ({binding['duration_s']} s)",
            )
        )

    br_count = bus.send_config_and_wait(mid, COMMAND_GET_BINARY_BIND_ROUTE_COUNT, timeout=0.4)
    if br_count is not None and len(br_count) >= 5 and int(br_count[2]) == 0:
        for idx in range(int(br_count[3])):
            rr = bus.send_config_and_wait(mid, COMMAND_GET_BINARY_BIND_ROUTE, [idx], timeout=0.35)
            if rr is None or len(rr) < 8 or int(rr[2]) != 0:
                continue
            rows.append(
                _row(
                    button=f"Sensor {int(rr[3])}",
                    action=_EDGE_NAME.get(int(rr[4]), f"edge {int(rr[4])}"),
                    receiver="Zdalny",
                    target_id=str(int(rr[5])),
                    target_type="Czujnik binarny",
                    target=str(int(rr[6])),
                    state=STATE_LABEL_BY_CODE.get(int(rr[7]), str(int(rr[7]))),
                )
            )

    sr_count = bus.send_config_and_wait(mid, COMMAND_GET_SHUTTER_BIND_ROUTE_COUNT, timeout=0.4)
    if sr_count is not None and len(sr_count) >= 5 and int(sr_count[2]) == 0:
        for idx in range(int(sr_count[3])):
            rr = bus.send_config_and_wait(mid, COMMAND_GET_SHUTTER_BIND_ROUTE, [idx], timeout=0.35)
            if rr is None or len(rr) < 8 or int(rr[2]) != 0:
                continue
            trigger = next(
                (k for k, v in SHUTTER_TRIGGER_LABELS.items() if v == (int(rr[4]), int(rr[5]))),
                f"trigger {int(rr[4])}:{int(rr[5])}",
            )
            rows.append(
                _row(
                    button=f"Roleta {int(rr[3])}",
                    action=str(trigger),
                    receiver="Zdalny",
                    target_id=str(int(rr[6])),
                    target_type="Stan rolety",
                    target=str(int(rr[7])),
                    state="Zalacz",
                )
            )

    se_count = bus.send_config_and_wait(mid, COMMAND_GET_SENSOR_BIND_ROUTE_COUNT, timeout=0.4)
    if se_count is not None and len(se_count) >= 5 and int(se_count[2]) == 0:
        for idx in range(int(se_count[3])):
            rr = bus.send_config_and_wait(mid, COMMAND_GET_SENSOR_BIND_ROUTE, [idx], timeout=0.35)
            if rr is None or len(rr) < 8 or int(rr[2]) != 0:
                continue
            kind = _SENSOR_KIND.get(int(rr[3]), f"Sensor {int(rr[3])}")
            rows.append(
                _row(
                    button=f"{kind} #{int(rr[4])}",
                    action=f"> {int(rr[5])}",
                    receiver="Zdalny",
                    target_id=str(int(rr[6])),
                    target_type="Wartosc sensora",
                    target=str(int(rr[7])),
                    state="Zalacz",
                )
            )

    bus.store_mappings(mid, rows)
    return {"ok": True, "module_id": mid, "mappings": rows, "count": len(rows)}
