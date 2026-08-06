from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .addon_client import CanGatewayAddonClient, addon_slug_matches, resolve_addon_base_url
from .const import (
    CONF_ADDON_API_URL,
    CONF_ADDON_SLUG,
    CONF_CONNECTION_MODE,
    CONF_DISCOVERED_MODULES,
    CONF_INITIAL_SCAN_DONE,
    CONF_SCAN_ON_SETUP,
    CONNECTION_MODE_ADDON,
    DEFAULT_ADDON_SLUG,
    DOMAIN,
)

CONF_TRIGGER_ADDON_SCAN = "trigger_addon_scan"


class CanGatewayV3ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _addon_base_url: str | None = None
    _addon_modules: list[dict]
    _addon_error: str | None = None

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        # This integration works exclusively with the CAN Gateway add-on
        # (Supervisor). There is only one connection mode, so skip straight
        # to the add-on step instead of showing a mode-selection form.
        return await self.async_step_addon(user_input)

    async def _resolve_addon_client(self) -> CanGatewayAddonClient | None:
        session = async_get_clientsession(self.hass)
        slug = DEFAULT_ADDON_SLUG
        base_url, _resolved = await resolve_addon_base_url(session, slug=slug, hass=self.hass)
        self._addon_base_url = base_url
        if base_url is None:
            self._addon_error = (
                "Dodatek CAN Gateway jest niedostępny. "
                "Zainstaluj i uruchom dodatek Supervisor (slug: can_gateway), "
                "następnie w panelu dodatku wykonaj skan magistrali."
            )
            return None
        self._addon_error = None
        return CanGatewayAddonClient(base_url, session)

    async def _load_addon_modules(self, client: CanGatewayAddonClient) -> list[dict]:
        modules = await client.get_modules()
        if not modules:
            discovery = await client.get_discovery()
            modules = [row for row in (discovery.get("modules") or []) if isinstance(row, dict)]
        return modules

    def _build_addon_entry_data(
        self,
        modules: list[dict],
        *,
        scan_on_setup: bool = False,
    ) -> dict:
        return {
            CONF_CONNECTION_MODE: CONNECTION_MODE_ADDON,
            CONF_ADDON_SLUG: DEFAULT_ADDON_SLUG,
            CONF_ADDON_API_URL: self._addon_base_url or "",
            CONF_SCAN_ON_SETUP: bool(scan_on_setup),
            CONF_INITIAL_SCAN_DONE: not bool(scan_on_setup),
            CONF_DISCOVERED_MODULES: modules,
        }

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> FlowResult:
        """Auto-setup when CAN Gateway Supervisor add-on publishes discovery."""
        if self._async_in_progress():
            return self.async_abort(reason="already_in_progress")

        if not addon_slug_matches(discovery_info.slug, DEFAULT_ADDON_SLUG):
            return self.async_abort(reason="not_can_gateway_addon")

        self._async_abort_entries_match({CONF_CONNECTION_MODE: CONNECTION_MODE_ADDON})

        client = await self._resolve_addon_client()
        if client is None:
            return self.async_abort(reason="addon_unreachable")

        try:
            modules = await self._load_addon_modules(client)
            await client.get_status()
        except Exception as err:  # noqa: BLE001
            return self.async_abort(reason="addon_read_failed")

        await self.async_set_unique_id(discovery_info.uuid)
        self._abort_if_unique_id_configured()

        entry_data = self._build_addon_entry_data(modules, scan_on_setup=False)
        return self.async_create_entry(
            title="CAN Gateway",
            data=entry_data,
        )

    async def async_step_addon(self, user_input: dict | None = None) -> FlowResult:
        client = await self._resolve_addon_client()
        if client is None:
            return self.async_show_form(
                step_id="addon",
                data_schema=vol.Schema({}),
                errors={"base": "addon_unreachable"},
                description_placeholders={"error": self._addon_error or "unknown"},
            )

        if user_input is not None:
            if user_input.get(CONF_TRIGGER_ADDON_SCAN):
                try:
                    result = await client.discovery_scan()
                    if not result.get("ok"):
                        self._addon_error = str(result.get("error", "scan failed"))
                except Exception as err:  # noqa: BLE001
                    self._addon_error = str(err)
                return await self.async_step_addon()

            try:
                modules = await self._load_addon_modules(client)
            except Exception as err:  # noqa: BLE001
                return self.async_show_form(
                    step_id="addon",
                    data_schema=vol.Schema({}),
                    errors={"base": "addon_read_failed"},
                    description_placeholders={"error": str(err)},
                )

            await self.async_set_unique_id(f"{DOMAIN}:addon:{DEFAULT_ADDON_SLUG}")
            self._abort_if_unique_id_configured()

            entry_data = self._build_addon_entry_data(
                modules,
                scan_on_setup=bool(user_input.get(CONF_SCAN_ON_SETUP, False)),
            )
            return self.async_create_entry(title="CAN Gateway (Add-on)", data=entry_data)

        try:
            modules = await self._load_addon_modules(client)
            status = await client.get_status()
        except Exception as err:  # noqa: BLE001
            return self.async_show_form(
                step_id="addon",
                data_schema=vol.Schema({}),
                errors={"base": "addon_read_failed"},
                description_placeholders={"error": str(err)},
            )

        self._addon_modules = modules
        lines = self._format_addon_modules_log(modules)
        bus_ok = bool(status.get("bus_ok"))
        bus_line = "magistrala CAN: połączona" if bus_ok else f"magistrala CAN: {status.get('bus_error', 'brak')}"

        schema = vol.Schema(
            {
                vol.Optional(CONF_TRIGGER_ADDON_SCAN, default=False): bool,
                vol.Optional(CONF_SCAN_ON_SETUP, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="addon",
            data_schema=schema,
            description_placeholders={
                "addon_url": self._addon_base_url or "",
                "modules_log": lines or "Brak zapisanych modułów — użyj „Skanuj w dodatku”.",
                "bus_status": bus_line,
                "module_count": str(len(modules)),
            },
        )

    def _format_addon_modules_log(self, modules: list[dict]) -> str:
        if not modules:
            return "Brak modułów w pamięci dodatku."
        lines: list[str] = []
        for mod in sorted(modules, key=lambda m: int(m.get("module_id", 0))):
            mid = mod.get("module_id")
            name = mod.get("name") or mod.get("hw_name") or "?"
            mac = mod.get("mac") or "?"
            fw = mod.get("firmware_build") or "?"
            lines.append(f"- Module {mid}: {name} | MAC {mac} | FW {fw}")
        return "\n".join(lines)
