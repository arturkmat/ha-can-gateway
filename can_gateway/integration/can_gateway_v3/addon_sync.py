"""Map add-on REST catalog onto CanGatewayCoordinator (add-on mode only)."""

from __future__ import annotations

import logging
import re
from typing import Any

from .const import (
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


def _sync_module_metadata(
    coordinator: CanGatewayCoordinator, modules: list[Any], *, catalog_only: bool = False
) -> None:
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

        coordinator._update_device_info(
            {
                "module_id": module_id,
                "hw_type": info.hw_type or 255,
                "hw_name": info.hw_name or "unknown",
                "mac": info.mac or "00:00:00:00:00:00",
            }
        )
        if not catalog_only:
            coordinator._touch_module_presence(module_id)

        rt = mod.get("runtime")
        if isinstance(rt, dict):
            _apply_runtime_maps(info, rt)
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


def apply_addon_state(
    coordinator: CanGatewayCoordinator,
    snapshot: dict[str, Any],
    *,
    catalog_only: bool = False,
) -> None:
    """Apply add-on module metadata + entity catalog. No duplicate entity derivation."""
    modules = snapshot.get("modules")
    if not isinstance(modules, list):
        return

    _sync_module_metadata(coordinator, modules, catalog_only=catalog_only)

    entities = snapshot.get("entities")
    if isinstance(entities, list) and entities:
        apply_addon_entities(coordinator, entities)
        return

    if catalog_only:
        _LOGGER.debug("Add-on catalog empty — skipping legacy entity synthesis")
        return

    _LOGGER.warning(
        "Add-on snapshot missing entities catalog; integration should use /api/entities"
    )


def seed_coordinator_from_modules(
    coordinator: CanGatewayCoordinator, modules: list[dict[str, Any]]
) -> None:
    """Apply persisted add-on module list during config flow / startup."""
    apply_addon_state(coordinator, {"modules": modules, "entities": []}, catalog_only=True)


def apply_addon_entity_values(
    coordinator: CanGatewayCoordinator, entities: list[Any]
) -> None:
    """Update live values/attributes without changing the entity catalog."""
    for raw in entities:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("unique_id") or "")
        if not uid or uid not in coordinator.entity_descriptions:
            continue
        attrs = raw.get("attributes")
        if not isinstance(attrs, dict):
            attrs = {}
        coordinator._set_state(uid, raw.get("value"), attrs)
