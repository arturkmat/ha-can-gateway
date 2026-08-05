"""HTTP client for CAN Gateway Home Assistant add-on."""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

from .const import DEFAULT_ADDON_SLUG

_LOGGER = logging.getLogger(__name__)

DEFAULT_ADDON_PORT = 8099


class CanGatewayAddonClient:
    def __init__(self, base_url: str, session: aiohttp.ClientSession) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session

    async def get_health(self) -> bool:
        try:
            async with self._session.get(
                f"{self.base_url}/api/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except aiohttp.ClientError:
            return False

    async def get_status(self) -> dict[str, Any]:
        async with self._session.get(
            f"{self.base_url}/api/status",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, dict) else {}

    async def get_modules(self) -> list[dict[str, Any]]:
        async with self._session.get(
            f"{self.base_url}/api/modules",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            rows = data.get("modules") if isinstance(data, dict) else None
            result = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            _LOGGER.debug(f"add-on API returned {len(result)} modules")
            return result

    async def get_discovery(self) -> dict[str, Any]:
        async with self._session.get(
            f"{self.base_url}/api/discovery",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, dict) else {}

    async def get_entities(self) -> dict[str, Any]:
        async with self._session.get(
            f"{self.base_url}/api/entities",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, dict) else {}

    async def get_state(self) -> dict[str, Any]:
        async with self._session.get(
            f"{self.base_url}/api/state",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, dict) else {}

    async def discovery_scan(self) -> dict[str, Any]:
        async with self._session.post(
            f"{self.base_url}/api/scan",
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            return await resp.json()

    async def refresh_module(self, module_id: int) -> dict[str, Any]:
        async with self._session.post(
            f"{self.base_url}/api/modules/{int(module_id)}/refresh",
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            return await resp.json()

    async def reboot_module(self, module_id: int) -> dict[str, Any]:
        async with self._session.post(
            f"{self.base_url}/api/modules/{int(module_id)}/reboot",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            return await resp.json()

    async def set_relay_state(self, module_id: int, relay_no: int, state: str) -> dict[str, Any]:
        async with self._session.post(
            f"{self.base_url}/api/modules/{int(module_id)}/relays/{int(relay_no)}",
            json={"state": state},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return await resp.json()

    async def set_shutter_command(
        self,
        module_id: int,
        shutter_no: int,
        command: str,
        param: int = 0,
    ) -> dict[str, Any]:
        async with self._session.post(
            f"{self.base_url}/api/modules/{int(module_id)}/shutters/{int(shutter_no)}",
            json={"command": command, "param": int(param)},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            return await resp.json()

    async def send_can_frame(self, can_id: int, data: list[int]) -> dict[str, Any]:
        async with self._session.post(
            f"{self.base_url}/api/can/send",
            json={"can_id": int(can_id), "data": [int(b) & 0xFF for b in data[:8]]},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            return await resp.json()


def _slug_matches(candidate: str, slug: str) -> bool:
    candidate = candidate.strip().lower()
    slug = slug.strip().lower()
    if not candidate or not slug:
        return False
    if candidate == slug:
        return True
    return candidate.endswith(f"_{slug}") or candidate.split("_")[-1] == slug


def _hostname_from_slug(addon_slug: str) -> str:
    return addon_slug.replace("_", "-")


def _base_urls_from_addon_info(info: dict[str, Any], *, default_port: int = DEFAULT_ADDON_PORT) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    port = int(info.get("ingress_port") or default_port)
    hostname = str(info.get("hostname") or "").strip()
    if hostname:
        url = f"http://{hostname}:{port}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    slug = str(info.get("slug") or "").strip()
    if slug:
        url = f"http://{_hostname_from_slug(slug)}:{port}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


async def _probe_base_urls(
    session: aiohttp.ClientSession,
    urls: list[str],
) -> str | None:
    for base in urls:
        client = CanGatewayAddonClient(base, session)
        if await client.get_health():
            _LOGGER.info("CAN Gateway add-on API at %s", base)
            return base
    return None


async def _supervisor_get_json(
    session: aiohttp.ClientSession,
    token: str,
    path: str,
) -> dict[str, Any] | None:
    try:
        async with session.get(
            f"http://supervisor{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            payload = await resp.json()
            if isinstance(payload, dict):
                data = payload.get("data", payload)
                return data if isinstance(data, dict) else None
    except aiohttp.ClientError as err:
        _LOGGER.debug("Supervisor request failed for %s: %s", path, err)
    return None


async def _find_addon_via_supervisor(
    session: aiohttp.ClientSession,
    token: str,
    slug: str,
) -> dict[str, Any] | None:
    info = await _supervisor_get_json(session, token, f"/addons/{slug}/info")
    if isinstance(info, dict) and info.get("slug"):
        return info

    listing = await _supervisor_get_json(session, token, "/addons")
    addons = listing.get("addons") if isinstance(listing, dict) else None
    if not isinstance(addons, list):
        return None

    matches: list[dict[str, Any]] = []
    for addon in addons:
        if not isinstance(addon, dict):
            continue
        aslug = str(addon.get("slug", ""))
        if _slug_matches(aslug, slug):
            matches.append(addon)

    if not matches:
        return None

    for addon in matches:
        if str(addon.get("state", "")).lower() == "started":
            full_slug = str(addon.get("slug", ""))
            detailed = await _supervisor_get_json(session, token, f"/addons/{full_slug}/info")
            return detailed if isinstance(detailed, dict) else addon

    full_slug = str(matches[0].get("slug", ""))
    detailed = await _supervisor_get_json(session, token, f"/addons/{full_slug}/info")
    return detailed if isinstance(detailed, dict) else matches[0]


async def _find_addon_via_hassio(hass, slug: str) -> dict[str, Any] | None:
    if "hassio" not in getattr(hass.config, "components", set()):
        return None
    try:
        from homeassistant.components.hassio import async_get_addon_info
    except ImportError:
        return None

    try:
        info = await async_get_addon_info(hass, slug)
    except Exception:  # noqa: BLE001
        info = None

    if isinstance(info, dict) and info.get("slug"):
        return info

    try:
        from homeassistant.components.hassio import get_addons_info
    except ImportError:
        return None

    try:
        addons = get_addons_info(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("get_addons_info failed", exc_info=True)
        return None

    matches: list[dict[str, Any]] = []
    for addon in addons.values() if isinstance(addons, dict) else []:
        if not isinstance(addon, dict):
            continue
        aslug = str(addon.get("slug", ""))
        if _slug_matches(aslug, slug):
            matches.append(addon)

    if not matches:
        return None

    async def _detailed(slug_value: str, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            detailed = await async_get_addon_info(hass, slug_value)
            if isinstance(detailed, dict):
                return detailed
        except Exception:  # noqa: BLE001
            pass
        return fallback

    for addon in matches:
        if str(addon.get("state", "")).lower() == "started":
            return await _detailed(str(addon.get("slug", "")), addon)
    return await _detailed(str(matches[0].get("slug", "")), matches[0])


def addon_slug_matches(candidate: str, slug: str) -> bool:
    """Return True when Supervisor add-on slug matches expected slug."""
    return _slug_matches(candidate, slug)


async def resolve_addon_base_url(
    session: aiohttp.ClientSession,
    *,
    slug: str = DEFAULT_ADDON_SLUG,
    override: str | None = None,
    hass=None,
) -> tuple[str | None, str | None]:
    """Return (base_url, resolved_full_slug)."""
    if override:
        base = override.rstrip("/")
        client = CanGatewayAddonClient(base, session)
        if await client.get_health():
            return base, slug

    addon_info: dict[str, Any] | None = None
    if hass is not None:
        addon_info = await _find_addon_via_hassio(hass, slug)

    if addon_info is None:
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if token:
            addon_info = await _find_addon_via_supervisor(session, token, slug)

    urls: list[str] = []
    resolved_slug = slug
    if isinstance(addon_info, dict):
        resolved_slug = str(addon_info.get("slug") or slug)
        state = str(addon_info.get("state", "")).lower()
        if state and state != "started":
            _LOGGER.warning(
                "CAN Gateway add-on slug=%s state=%s (expected started)",
                resolved_slug,
                state,
            )
        urls.extend(_base_urls_from_addon_info(addon_info))

    for fallback_slug in (resolved_slug, slug, f"local_{slug}"):
        url = f"http://{_hostname_from_slug(fallback_slug)}:{DEFAULT_ADDON_PORT}"
        if url not in urls:
            urls.append(url)

    base_url = await _probe_base_urls(session, urls)
    if base_url is None:
        _LOGGER.error(
            "CAN Gateway add-on unreachable (slug=%s, tried=%s)",
            slug,
            ", ".join(urls),
        )
    return base_url, resolved_slug if base_url else None
