"""Setup integration as client of CAN Gateway add-on (no direct USB)."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .addon_client import CanGatewayAddonClient, resolve_addon_base_url
from .addon_sync import (
    addon_catalog_ready,
    apply_addon_entity_values,
    apply_addon_state,
    clear_addon_entities,
    seed_coordinator_from_modules,
)
from .const import (
    CONF_ADDON_API_URL,
    CONF_ADDON_SLUG,
    CONF_DISCOVERED_MODULES,
    CONF_INITIAL_SCAN_DONE,
    CONF_SCAN_ON_SETUP,
    DATA_ADDON_CLIENT,
    DATA_CAN_SEND,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import CanGatewayCoordinator
from .protocol import (
    CAN_V2_CLASS_CONFIG_REQUEST,
    CAN_V2_CLASS_CONTROL_COMMAND,
    V2_CTRL_SHUTTER_CMD,
    can_v2_frame_module_id,
)

_LOGGER = logging.getLogger(__name__)

CORE_PLATFORMS = tuple(PLATFORMS)
ENTITY_POLL_INTERVAL = timedelta(seconds=5)
DISCOVERY_POLL_INTERVAL = timedelta(seconds=8)
WAITING_FOR_SCAN_DETAILS = (
    "Waiting for add-on scan — open CAN Gateway panel and run bus scan"
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from . import _register_services

    session = async_get_clientsession(hass)
    slug = str(entry.data.get(CONF_ADDON_SLUG, "can_gateway"))
    override = str(entry.data.get(CONF_ADDON_API_URL, "")).strip() or None
    base_url, resolved_slug = await resolve_addon_base_url(
        session, slug=slug, override=override, hass=hass
    )
    if base_url is None:
        _LOGGER.error(
            "Cannot connect to CAN Gateway add-on — start the add-on first (slug=%s)",
            slug,
        )
        return False

    client = CanGatewayAddonClient(base_url, session)
    coordinator = CanGatewayCoordinator(hass)

    runtime_data: dict = {
        "coordinator": coordinator,
        DATA_ADDON_CLIENT: client,
    }
    last_discovery_version: int | None = None
    reload_lock = asyncio.Lock()

    async def _persist_modules(modules: list[dict]) -> None:
        if not modules:
            return
        new_data = dict(entry.data)
        new_data[CONF_DISCOVERED_MODULES] = [row for row in modules if isinstance(row, dict)]
        hass.config_entries.async_update_entry(entry, data=new_data)

    async def _apply_catalog(discovery: dict, entities_payload: dict) -> bool:
        modules = [row for row in (discovery.get("modules") or []) if isinstance(row, dict)]
        apply_addon_state(
            coordinator,
            {"modules": modules, "entities": []},
            catalog_only=True,
        )
        await _persist_modules(modules)

        if not addon_catalog_ready(discovery):
            clear_addon_entities(coordinator)
            coordinator.mark_scan_finished("waiting", WAITING_FOR_SCAN_DETAILS)
            return False

        entities = [
            row for row in (entities_payload.get("entities") or []) if isinstance(row, dict)
        ]
        if not entities:
            clear_addon_entities(coordinator)
            coordinator.mark_scan_finished("waiting", WAITING_FOR_SCAN_DETAILS)
            return False

        apply_addon_state(
            coordinator,
            {"modules": modules, "entities": entities},
            catalog_only=True,
        )
        coordinator.mark_scan_finished(
            "ok",
            f"Catalog v{int(discovery.get('discovery_version') or 0)}: "
            f"{len(entities)} entities from add-on",
        )
        return True

    async def _reload_platforms() -> None:
        async with reload_lock:
            await hass.config_entries.async_unload_platforms(entry, list(CORE_PLATFORMS))
            for platform in CORE_PLATFORMS:
                try:
                    await hass.config_entries.async_forward_entry_setups(entry, (platform,))
                except Exception:  # noqa: BLE001
                    _LOGGER.warning("Platform '%s' failed to reload", platform, exc_info=True)

    async def _poll_entities(_now=None) -> None:
        try:
            discovery = await client.get_discovery()
            if not addon_catalog_ready(discovery):
                return
            payload = await client.get_entities()
            entities = payload.get("entities")
            if not isinstance(entities, list) or not entities:
                return
            apply_addon_entity_values(coordinator, entities)
            coordinator.notify_gateway_state()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Add-on entity value poll failed", exc_info=True)

    async def _poll_discovery(_now=None) -> None:
        nonlocal last_discovery_version
        try:
            discovery = await client.get_discovery()
            version = discovery.get("discovery_version")
            if version is None:
                return
            version_int = int(version)
            if last_discovery_version is not None and version_int == last_discovery_version:
                return
            last_discovery_version = version_int
            if not addon_catalog_ready(discovery):
                _LOGGER.info(
                    "Add-on discovery v%s has no entity catalog yet — waiting for scan",
                    version_int,
                )
                entities_payload: dict = {"entities": []}
            else:
                _LOGGER.info(
                    "Add-on discovery changed (version=%s, modules=%s, entities=%s)",
                    version_int,
                    discovery.get("module_count"),
                    discovery.get("entity_count"),
                )
                entities_payload = await client.get_entities()
            catalog_applied = await _apply_catalog(discovery, entities_payload)
            if catalog_applied:
                await _reload_platforms()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Add-on discovery poll failed", exc_info=True)

    async def _send_can(can_id: int, data: list[int], ext: bool = False, rtr: bool = False) -> None:
        del ext, rtr
        if len(data) < 2:
            return

        frame_class = can_id & 0x07
        if (
            frame_class == CAN_V2_CLASS_CONTROL_COMMAND
            and len(data) >= 4
            and int(data[0]) == V2_CTRL_SHUTTER_CMD
        ):
            module_id = can_v2_frame_module_id(can_id)
            cmd_map = {1: "open", 2: "close", 3: "stop", 4: "position"}
            command = cmd_map.get(int(data[2]), "stop")
            param = int(data[3]) if int(data[2]) == 4 else 0
            shutter_no = int(data[1])
            result = await client.set_shutter_command(module_id, shutter_no, command, param)
            if not result.get("ok"):
                _LOGGER.warning(
                    "Shutter command failed module=%s shutter=%s: %s",
                    module_id,
                    shutter_no,
                    result.get("error", result),
                )
            await _poll_entities()
            return

        module_id = int(data[0])
        cmd = int(data[1]) if len(data) > 1 else 0
        if frame_class == CAN_V2_CLASS_CONFIG_REQUEST:
            if cmd == 59 and len(data) >= 4:
                state = {0: "off", 1: "on", 2: "toggle"}.get(int(data[3]), "toggle")
                await client.set_relay_state(module_id, int(data[2]), state)
                await _poll_entities()
                return
            if cmd == 1 and len(data) >= 2:
                await client.reboot_module(module_id)
                return
        result = await client.send_can_frame(can_id, data)
        if not result.get("ok"):
            _LOGGER.debug(
                "Add-on CAN send failed can_id=0x%X module=%s cmd=%s: %s",
                can_id,
                module_id,
                cmd,
                result.get("error", result),
            )

    runtime_data[DATA_CAN_SEND] = _send_can
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime_data
    _register_services(hass, entry, _send_can)

    for platform in CORE_PLATFORMS:
        try:
            await hass.config_entries.async_forward_entry_setups(entry, (platform,))
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Platform '%s' failed to load", platform, exc_info=True)

    catalog_ready = False
    try:
        initial_discovery = await client.get_discovery()
        if initial_discovery.get("discovery_version") is not None:
            last_discovery_version = int(initial_discovery["discovery_version"])
        entities_payload = (
            await client.get_entities()
            if addon_catalog_ready(initial_discovery)
            else {"entities": []}
        )
        catalog_ready = await _apply_catalog(initial_discovery, entities_payload)
        if catalog_ready:
            await _reload_platforms()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Initial add-on catalog load failed", exc_info=True)
        coordinator.mark_scan_finished("waiting", WAITING_FOR_SCAN_DETAILS)

    @callback
    def _track_entities(_now) -> None:
        hass.async_create_task(_poll_entities())

    @callback
    def _track_discovery(_now) -> None:
        hass.async_create_task(_poll_discovery())

    entry.async_on_unload(async_track_time_interval(hass, _track_entities, ENTITY_POLL_INTERVAL))
    entry.async_on_unload(async_track_time_interval(hass, _track_discovery, DISCOVERY_POLL_INTERVAL))

    if bool(entry.data.get(CONF_SCAN_ON_SETUP, True)) and not bool(
        entry.data.get(CONF_INITIAL_SCAN_DONE)
    ):

        async def _startup_scan() -> None:
            coordinator.mark_scan_started("addon_startup_scan")
            result: dict = {"ok": False}
            try:
                result = await client.discovery_scan()
                if not result.get("ok"):
                    _LOGGER.warning(
                        "Add-on startup scan failed: %s",
                        result.get("error", result),
                    )
                await _poll_discovery()
                await _poll_entities()
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Add-on startup scan failed", exc_info=True)
                coordinator.mark_scan_finished("waiting", WAITING_FOR_SCAN_DETAILS)
            else:
                new_data = dict(entry.data)
                new_data[CONF_INITIAL_SCAN_DONE] = True
                hass.config_entries.async_update_entry(entry, data=new_data)
                if addon_catalog_ready(await client.get_discovery()):
                    coordinator.mark_scan_finished(
                        "ok",
                        f"Add-on startup scan finished, modules={len(coordinator.scanned_modules)}",
                    )
                else:
                    coordinator.mark_scan_finished("waiting", WAITING_FOR_SCAN_DETAILS)

        hass.async_create_task(_startup_scan())
    elif catalog_ready:
        coordinator.mark_scan_finished(
            "ok",
            f"Using add-on persisted catalog, modules={len(coordinator.scanned_modules)}",
        )

    _LOGGER.info(
        "CAN Gateway (add-on client) connected to %s slug=%s — catalog from /api/entities",
        base_url,
        resolved_slug or slug,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, list(CORE_PLATFORMS))
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime:
        coordinator = runtime.get("coordinator")
        if coordinator:
            coordinator.clear_all_entities()
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return unload_ok
