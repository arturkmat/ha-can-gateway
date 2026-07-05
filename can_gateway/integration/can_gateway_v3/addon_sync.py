"""Map add-on /api/state snapshot onto CanGatewayCoordinator."""

from __future__ import annotations

import logging
import re
from typing import Any

from .const import (
    EVENT_DEVICE_INFO,
    EVENT_RELAY,
    EVENT_RELAY_MCP23017,
    EVENT_SHUTTER,
    MCP23017_RELAY_CAN_BASE,
)
from .coordinator import CanGatewayCoordinator, EntityDescription

_LOGGER = logging.getLogger(__name__)

_SUMMARY_RE = re.compile(
    r"buttons=(\d+)\s+relays=(\d+)(?:\s+ds18=\d+)?\s+shutters=(\d+)",
    re.IGNORECASE,
)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_summary_counts(info, mod: dict[str, Any]) -> None:
    if mod.get("button_count") is not None:
        info.button_count = _int_or_none(mod.get("button_count"))
    if mod.get("relay_count") is not None:
        info.relay_count = _int_or_none(mod.get("relay_count"))
    if mod.get("shutter_count") is not None:
        info.shutter_count = _int_or_none(mod.get("shutter_count"))
    details = str(mod.get("summary_details") or "")
    if details and info.relay_count is None:
        match = _SUMMARY_RE.search(details)
        if match:
            info.button_count = int(match.group(1))
            info.relay_count = int(match.group(2))
            info.shutter_count = int(match.group(3))


def _apply_runtime_maps(info, rt: dict[str, Any]) -> None:
    relay_gpio = rt.get("relay_gpio_map")
    if isinstance(relay_gpio, dict):
        for k, v in relay_gpio.items():
            try:
                info.relay_gpio_map[int(k)] = int(v)
            except (TypeError, ValueError):
                pass

    shutter_map = rt.get("shutter_map")
    if isinstance(shutter_map, dict):
        for k, pair in shutter_map.items():
            try:
                sid = int(k)
                if isinstance(pair, list) and len(pair) >= 2:
                    ro = CanGatewayCoordinator.normalize_shutter_relay_no(int(pair[0]))
                    rc = CanGatewayCoordinator.normalize_shutter_relay_no(int(pair[1]))
                    if ro > 0 or rc > 0:
                        info.shutter_relay_map[sid] = (ro, rc)
                    else:
                        info.shutter_relay_map.pop(sid, None)
            except (TypeError, ValueError):
                pass

    mcp_pins = rt.get("mcp_relay_pins")
    if isinstance(mcp_pins, dict):
        parsed: dict[int, set[int]] = {}
        for chip, pins in mcp_pins.items():
            try:
                chip_i = int(chip)
            except (TypeError, ValueError):
                continue
            if isinstance(pins, (list, tuple, set)):
                parsed[chip_i] = {int(p) for p in pins}
        if parsed:
            info.mcp_relay_pins_by_chip = parsed


def _sync_module_metadata(coordinator: CanGatewayCoordinator, modules: list[Any]) -> None:
    for mod in modules:
        if not isinstance(mod, dict):
            continue
        module_id = mod.get("module_id")
        if not isinstance(module_id, int) or not (1 <= module_id <= 254):
            continue

        coordinator.scanned_modules.add(module_id)
        info = coordinator.get_module_info(module_id)
        if mod.get("name"):
            info.name = str(mod["name"])
        if mod.get("hw_type") is not None:
            info.hw_type = int(mod["hw_type"])
        if mod.get("hw_name"):
            info.hw_name = str(mod["hw_name"])
        if mod.get("mac"):
            info.mac = str(mod["mac"])
        if mod.get("firmware_build"):
            info.firmware_build_datetime = str(mod["firmware_build"])

        _apply_summary_counts(info, mod)

        coordinator.update_from_event(
            EVENT_DEVICE_INFO,
            {
                "module_id": module_id,
                "hw_type": info.hw_type or 255,
                "hw_name": info.hw_name or "unknown",
                "mac": info.mac or "00:00:00:00:00:00",
            },
        )
        coordinator._touch_module_presence(module_id)

        rt = mod.get("runtime")
        if isinstance(rt, dict):
            _apply_runtime_maps(info, rt)
            if isinstance(info.button_count, int) and info.button_count > 0:
                coordinator._ensure_button_entities(module_id, info.button_count)
            if isinstance(info.shutter_count, int) and info.shutter_count > 0:
                coordinator._ensure_shutter_entities(module_id, info.shutter_count)
            coordinator.prune_switches_mapped_to_any_shutter(module_id)


def apply_addon_entities(coordinator: CanGatewayCoordinator, entities: list[Any]) -> None:
    incoming_uids: set[str] = set()
    for raw in entities:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("unique_id") or "")
        platform = str(raw.get("platform") or "")
        name = str(raw.get("name") or uid)
        module_id = raw.get("module_id")
        if not uid or not platform or not isinstance(module_id, int):
            continue

        incoming_uids.add(uid)
        desc = EntityDescription(
            platform=platform,
            unique_id=uid,
            name=name,
            module_id=int(module_id),
            device_class=raw.get("device_class"),
            unit=raw.get("unit"),
            icon=raw.get("icon"),
        )
        if uid not in coordinator.entity_descriptions:
            coordinator._ensure_entity(desc)
        else:
            coordinator.entity_descriptions[uid] = desc

        attrs = raw.get("attributes")
        if not isinstance(attrs, dict):
            attrs = {}
        coordinator._set_state(uid, raw.get("value"), attrs)

    removed = set(coordinator.entity_descriptions.keys()) - incoming_uids
    if removed:
        for uid in removed:
            coordinator.entity_descriptions.pop(uid, None)
            coordinator.entity_states.pop(uid, None)
        coordinator._notify_switch_prune_listeners()


def _apply_control_relays(coordinator: CanGatewayCoordinator, module_id: int, rows: list[Any]) -> None:
    local_rows: list[dict[str, Any]] = []
    mcp_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("shutter_reserved"):
            continue
        rn = row.get("relay_no")
        if rn is None:
            continue
        relay_no = int(rn)
        entry = {"relay_no": relay_no, "state": "ON" if row.get("on") else "OFF"}
        source = str(row.get("source") or "local")
        if source == "mcp23017":
            chip_offset = (relay_no - MCP23017_RELAY_CAN_BASE) // 16
            local_pin = (relay_no - MCP23017_RELAY_CAN_BASE) % 16
            entry["chip_offset"] = chip_offset
            entry["local_pin"] = local_pin
            mcp_rows.append(entry)
        else:
            local_rows.append(entry)
    if local_rows:
        coordinator.update_from_event(EVENT_RELAY, {"module_id": module_id, "relays": local_rows})
    if mcp_rows:
        coordinator.update_from_event(EVENT_RELAY_MCP23017, {"module_id": module_id, "relays": mcp_rows})


def _apply_relay_rows(coordinator: CanGatewayCoordinator, module_id: int, rt: dict[str, Any]) -> None:
    relays = rt.get("relays")
    if not isinstance(relays, list):
        return
    local_rows: list[dict[str, Any]] = []
    mcp_rows: list[dict[str, Any]] = []
    for row in relays:
        if not isinstance(row, dict):
            continue
        rn = row.get("relay_no")
        if rn is None:
            continue
        relay_no = int(rn)
        entry = {"relay_no": relay_no, "state": "ON" if row.get("on") else "OFF"}
        source = str(row.get("source") or "local")
        if source == "mcp23017":
            chip_offset = (relay_no - MCP23017_RELAY_CAN_BASE) // 16
            local_pin = (relay_no - MCP23017_RELAY_CAN_BASE) % 16
            entry["chip_offset"] = chip_offset
            entry["local_pin"] = local_pin
            mcp_rows.append(entry)
        else:
            local_rows.append(entry)
    if local_rows:
        coordinator.update_from_event(EVENT_RELAY, {"module_id": module_id, "relays": local_rows})
    if mcp_rows:
        coordinator.update_from_event(EVENT_RELAY_MCP23017, {"module_id": module_id, "relays": mcp_rows})


def _apply_shutter_rows(coordinator: CanGatewayCoordinator, module_id: int, rt: dict[str, Any]) -> None:
    shutters = rt.get("shutters")
    if not isinstance(shutters, list):
        return
    for sh in shutters:
        if not isinstance(sh, dict):
            continue
        sid = sh.get("shutter_no")
        if sid is None:
            continue
        coordinator.update_from_event(
            EVENT_SHUTTER,
            {
                "module_id": module_id,
                "shutter_no": int(sid),
                "position": sh.get("position"),
                "direction": sh.get("direction", 0),
                "direction_text": sh.get("direction_text", "stopped"),
            },
        )


def _apply_legacy_module_entities(coordinator: CanGatewayCoordinator, mod: dict[str, Any]) -> None:
    module_id = mod.get("module_id")
    if not isinstance(module_id, int):
        return
    rt = mod.get("runtime")
    if isinstance(rt, dict):
        _apply_shutter_rows(coordinator, module_id, rt)

    control_relays = mod.get("control_relays")
    if isinstance(control_relays, list):
        _apply_control_relays(coordinator, module_id, control_relays)
    elif isinstance(rt, dict):
        _apply_relay_rows(coordinator, module_id, rt)


def apply_addon_state(coordinator: CanGatewayCoordinator, snapshot: dict[str, Any]) -> None:
    modules = snapshot.get("modules")
    if not isinstance(modules, list):
        return

    _sync_module_metadata(coordinator, modules)

    entities = snapshot.get("entities")
    if isinstance(entities, list) and entities:
        apply_addon_entities(coordinator, entities)
        return

    for mod in modules:
        if isinstance(mod, dict):
            _apply_legacy_module_entities(coordinator, mod)


def seed_coordinator_from_modules(
    coordinator: CanGatewayCoordinator, modules: list[dict[str, Any]]
) -> None:
    """Apply persisted add-on module list during config flow / startup."""
    apply_addon_state(coordinator, {"modules": modules, "entities": []})
