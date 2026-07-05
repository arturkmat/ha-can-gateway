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
from .addon_sync import apply_addon_state, seed_coordinator_from_modules
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

_LOGGER = logging.getLogger(__name__)

CORE_PLATFORMS = tuple(PLATFORMS)
POLL_INTERVAL = timedelta(seconds=5)


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

    discovered = entry.data.get(CONF_DISCOVERED_MODULES)
    if isinstance(discovered, list) and discovered:
        try:
            seed_coordinator_from_modules(
                coordinator,
                [row for row in discovered if isinstance(row, dict)],
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not seed modules from config entry", exc_info=True)

    runtime_data: dict = {
        "coordinator": coordinator,
        DATA_ADDON_CLIENT: client,
    }

    async def _poll_state(_now=None) -> None:
        try:
            snapshot = await client.get_state()
            apply_addon_state(coordinator, snapshot)
            coordinator.notify_gateway_state()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Add-on state poll failed", exc_info=True)

    async def _send_can(can_id: int, data: list[int], ext: bool = False, rtr: bool = False) -> None:
        del ext, rtr
        if len(data) < 2:
            return
        module_id = int(data[0])
        cmd = int(data[1]) if len(data) > 1 else 0
        if cmd == 59 and len(data) >= 4:
            state = {0: "off", 1: "on", 2: "toggle"}.get(int(data[3]), "toggle")
            await client.set_relay_state(module_id, int(data[2]), state)
            await _poll_state()
            return
        if cmd == 1 and len(data) >= 2:
            await client.reboot_module(module_id)
            return
        if can_id & 0x7 == 2 and len(data) >= 4:
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
            await _poll_state()
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

    await _poll_state()

    @callback
    def _track_poll(_now) -> None:
        hass.async_create_task(_poll_state())

    entry.async_on_unload(async_track_time_interval(hass, _track_poll, POLL_INTERVAL))

    if bool(entry.data.get(CONF_SCAN_ON_SETUP, True)) and not bool(
        entry.data.get(CONF_INITIAL_SCAN_DONE)
    ):

        async def _startup_scan() -> None:
            coordinator.mark_scan_started("addon_startup_scan")
            try:
                await client.discovery_scan()
                await _poll_state()
                for module_id in coordinator.get_known_module_ids():
                    try:
                        await client.refresh_module(module_id)
                        await asyncio.sleep(0.35)
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Add-on refresh module %s failed", module_id, exc_info=True)
                await _poll_state()
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Add-on startup scan failed", exc_info=True)
                coordinator.mark_scan_finished("error", "Add-on startup scan failed")
            else:
                new_data = dict(entry.data)
                new_data[CONF_INITIAL_SCAN_DONE] = True
                new_data[CONF_DISCOVERED_MODULES] = await client.get_modules()
                hass.config_entries.async_update_entry(entry, data=new_data)
                coordinator.mark_scan_finished(
                    "ok",
                    f"Add-on startup scan finished, modules={len(coordinator.scanned_modules)}",
                )

        hass.async_create_task(_startup_scan())
    else:
        coordinator.mark_scan_finished(
            "ok",
            f"Using add-on persisted modules={len(coordinator.scanned_modules)}",
        )

    _LOGGER.info(
        "CAN Gateway v3 (add-on client) connected to %s slug=%s",
        base_url,
        resolved_slug or slug,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, list(CORE_PLATFORMS))
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if DOMAIN in hass.data and not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return unload_ok
