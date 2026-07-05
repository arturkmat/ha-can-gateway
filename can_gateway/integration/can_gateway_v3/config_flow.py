from __future__ import annotations

import asyncio
import glob
import os
from collections import deque

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import persistent_notification
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .addon_client import CanGatewayAddonClient, addon_slug_matches, resolve_addon_base_url
from .can_io import SlcanSerialBridge
from .const import (
    CONF_ADDON_API_URL,
    CONF_ADDON_SLUG,
    CONF_CAN_BITRATE,
    CONF_CONNECTION_MODE,
    CONF_DISCOVERED_MODULES,
    CONF_INITIAL_SCAN_DONE,
    CONF_SCAN_ON_SETUP,
    CONF_SERIAL_BAUDRATE,
    CONF_SERIAL_PORT,
    CONNECTION_MODE_ADDON,
    CONNECTION_MODE_SERIAL,
    DEFAULT_ADDON_SLUG,
    DEFAULT_CAN_BITRATE,
    DEFAULT_SERIAL_BAUDRATE,
    DEFAULT_SERIAL_PORT,
    DOMAIN,
    EVENT_CONFIG_RESPONSE,
    EVENT_DEVICE_INFO,
    EVENT_FRAME,
)
from .parser import events_from_payload
from .protocol import (
    COMMAND_GET_MODULE_NAME,
    can_v2_config_request_id,
    is_config_response_frame,
    module_name_read_offsets,
)

CONF_SCAN_DURING_ADD = "scan_during_add"
CONF_SCAN_DURATION_SEC = "scan_duration_sec"
CONF_SCAN_INTERVAL_SEC = "scan_interval_sec"
CONF_SCAN_DEEP = "scan_deep"
CONF_TRIGGER_ADDON_SCAN = "trigger_addon_scan"

SCAN_DURATION_MIN = 120
SCAN_DURATION_MAX = 300
SCAN_INTERVAL_MIN = 2
SCAN_INTERVAL_MAX = 30


class CanGatewayV3ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _scan_task: asyncio.Task[None] | None = None
    _found_modules: set[int]
    _module_names: dict[int, str]
    _scan_results_text: str
    _scan_diag_text: str = ""
    _scan_started_monotonic: float | None = None
    _scan_duration_s: int = 0
    _scan_stage_text: str = "idle"
    _scan_notification_id = "can_gateway_v3_scan_progress"
    _addon_base_url: str | None = None
    _addon_modules: list[dict]
    _addon_error: str | None = None

    def _supervisor_available(self) -> bool:
        return "hassio" in getattr(self.hass.config, "components", set())

    def _detect_default_serial_port(self) -> str:
        candidates = sorted(glob.glob("/dev/serial/by-id/*"))
        if candidates:
            return candidates[0]
        for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
            ports = sorted(glob.glob(pattern))
            if ports:
                return ports[0]
        for n in range(3, 33):
            name = f"COM{n}"
            if os.path.exists(f"\\\\.\\{name}"):
                return name
        return DEFAULT_SERIAL_PORT

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            mode = str(user_input.get(CONF_CONNECTION_MODE, CONNECTION_MODE_ADDON))
            self.context["connection_mode"] = mode
            if mode == CONNECTION_MODE_ADDON:
                return await self.async_step_addon()
            return await self.async_step_serial()

        choices = [CONNECTION_MODE_ADDON, CONNECTION_MODE_SERIAL]
        default_mode = CONNECTION_MODE_ADDON if self._supervisor_available() else CONNECTION_MODE_SERIAL
        schema = vol.Schema(
            {
                vol.Required(CONF_CONNECTION_MODE, default=default_mode): vol.In(choices),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

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
            title="CAN Gateway v3",
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
            return self.async_create_entry(title="CAN Gateway v3 (Add-on)", data=entry_data)

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

    async def async_step_serial(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            unique_ref = user_input.get(CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT)
            await self.async_set_unique_id(f"{DOMAIN}:serial:{unique_ref}")
            self._abort_if_unique_id_configured()

            user_input[CONF_SCAN_ON_SETUP] = True
            user_input[CONF_CONNECTION_MODE] = CONNECTION_MODE_SERIAL

            self._found_modules = set()
            self._module_names = {}
            self._scan_results_text = "No modules found."
            self._scan_diag_text = ""
            self.context["flow_user_input"] = dict(user_input)

            if user_input.get(CONF_SCAN_DURING_ADD, False):
                return await self.async_step_scan_progress()
            entry_data = dict(user_input)
            entry_data[CONF_INITIAL_SCAN_DONE] = False
            entry_data.pop(CONF_SCAN_DURING_ADD, None)
            entry_data.pop(CONF_SCAN_DEEP, None)
            entry_data.pop(CONF_SCAN_DURATION_SEC, None)
            entry_data.pop(CONF_SCAN_INTERVAL_SEC, None)
            return self.async_create_entry(title="CAN Gateway v3", data=entry_data)

        schema = vol.Schema(
            {
                vol.Required(CONF_SERIAL_PORT, default=self._detect_default_serial_port()): str,
                vol.Required(CONF_SERIAL_BAUDRATE, default=DEFAULT_SERIAL_BAUDRATE): vol.All(
                    vol.Coerce(int), vol.Range(min=9600, max=3000000)
                ),
                vol.Required(CONF_CAN_BITRATE, default=DEFAULT_CAN_BITRATE): vol.In(
                    [125000, 250000, 500000]
                ),
                vol.Required(CONF_SCAN_ON_SETUP, default=True): bool,
                vol.Required(CONF_SCAN_DURING_ADD, default=False): bool,
                vol.Required(CONF_SCAN_DEEP, default=False): bool,
                vol.Required(CONF_SCAN_DURATION_SEC, default=180): vol.All(
                    vol.Coerce(int), vol.Range(min=SCAN_DURATION_MIN, max=SCAN_DURATION_MAX)
                ),
                vol.Required(CONF_SCAN_INTERVAL_SEC, default=8): vol.All(
                    vol.Coerce(int), vol.Range(min=SCAN_INTERVAL_MIN, max=SCAN_INTERVAL_MAX)
                ),
            }
        )
        return self.async_show_form(step_id="serial", data_schema=schema)

    async def async_step_scan_progress(self, user_input: dict | None = None) -> FlowResult:
        if self._scan_task is None:
            cfg = self.context.get("flow_user_input", {})
            port = str(cfg.get(CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT))
            serial_baud = int(cfg.get(CONF_SERIAL_BAUDRATE, DEFAULT_SERIAL_BAUDRATE))
            can_bitrate = int(cfg.get(CONF_CAN_BITRATE, DEFAULT_CAN_BITRATE))
            duration_s = int(cfg.get(CONF_SCAN_DURATION_SEC, 180))
            interval_s = int(cfg.get(CONF_SCAN_INTERVAL_SEC, 8))
            deep_scan = bool(cfg.get(CONF_SCAN_DEEP, False))
            self._scan_duration_s = duration_s
            self._scan_started_monotonic = asyncio.get_running_loop().time()
            self._scan_task = self.hass.async_create_task(
                self._run_discovery_scan(
                    port, serial_baud, can_bitrate, duration_s, interval_s, deep_scan
                )
            )

        if self._scan_task.done():
            return self.async_show_progress_done(next_step_id="finish")

        remaining_s = self._calc_scan_remaining_seconds()
        placeholders = {
            "remaining_seconds": str(remaining_s),
            "found_count": str(len(self._found_modules)),
            "scan_stage": self._scan_stage_text,
        }
        return self.async_show_progress(
            step_id="scan_progress",
            progress_action="scan_modules",
            progress_task=self._scan_task,
            description_placeholders=placeholders,
        )

    async def async_step_finish(self, user_input: dict | None = None) -> FlowResult:
        cfg = dict(self.context.get("flow_user_input", {}))
        cfg[CONF_CONNECTION_MODE] = CONNECTION_MODE_SERIAL
        if user_input is not None:
            cfg[CONF_INITIAL_SCAN_DONE] = bool(cfg.get(CONF_SCAN_DURING_ADD, True))
            cfg.pop(CONF_SCAN_DURING_ADD, None)
            cfg.pop(CONF_SCAN_DEEP, None)
            cfg.pop(CONF_SCAN_DURATION_SEC, None)
            cfg.pop(CONF_SCAN_INTERVAL_SEC, None)
            return self.async_create_entry(title="CAN Gateway v3", data=cfg)

        placeholders = {"modules_log": self._scan_results_text}
        if self._scan_diag_text:
            placeholders["modules_log"] = f"{self._scan_results_text}\n\n{self._scan_diag_text}"
        return self.async_show_form(
            step_id="finish",
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
        )

    async def _run_discovery_scan(
        self,
        port: str,
        serial_baud: int,
        can_bitrate: int,
        duration_s: int,
        interval_s: int,
        deep_scan: bool,
    ) -> None:
        touched_ids: set[int] = set()
        rx_events: deque[tuple[str, dict]] = deque()
        received_frames = 0
        info_frames = 0
        summary_frames = 0
        self._found_modules = set()
        self._module_names = {}

        def _on_raw_payload(raw_payload: str) -> None:
            nonlocal received_frames, info_frames, summary_frames
            try:
                events = events_from_payload(raw_payload)
            except Exception:  # noqa: BLE001
                return
            for event_type, payload in events:
                rx_events.append((event_type, payload))
                if event_type == EVENT_FRAME:
                    received_frames += 1
                if event_type == EVENT_DEVICE_INFO:
                    info_frames += 1
                if event_type == EVENT_CONFIG_RESPONSE and int(payload.get("command", -1)) == 3:
                    summary_frames += 1
                module_id = payload.get("module_id")
                if isinstance(module_id, int) and 1 <= module_id <= 254:
                    self._found_modules.add(module_id)
                    touched_ids.add(module_id)
                can_id = payload.get("can_id")
                if (
                    isinstance(can_id, int)
                    and is_config_response_frame(can_id)
                    and payload.get("command") == 37
                    and isinstance(payload.get("response_decoded"), dict)
                ):
                    name = payload["response_decoded"].get("module_name")
                    if isinstance(name, str) and name and isinstance(module_id, int):
                        self._module_names[module_id] = name
                self._scan_results_text = self._format_modules_log()

        def _drain_rx_events() -> None:
            rx_events.clear()

        def _notify_progress() -> None:
            remaining_s = self._calc_scan_remaining_seconds()
            persistent_notification.async_create(
                self.hass,
                (
                    "CAN scan in progress\n\n"
                    f"Stage: {self._scan_stage_text}\n"
                    f"Remaining: {remaining_s}s\n"
                    f"Found modules: {len(self._found_modules)}\n"
                    f"Frames: received={received_frames}, info_701={info_frames}, summary_711={summary_frames}"
                ),
                title="CAN Gateway v3 scan",
                notification_id=self._scan_notification_id,
            )

        async def _wait_for_config_response(target_id: int, command: int, timeout: float) -> dict | None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                if rx_events:
                    event_type, payload = rx_events.popleft()
                    if event_type != EVENT_CONFIG_RESPONSE:
                        continue
                    if int(payload.get("command", -1)) != int(command):
                        continue
                    module_id = payload.get("module_id")
                    if target_id != 0xFF and int(module_id or 0) != int(target_id):
                        continue
                    return payload
                await asyncio.sleep(0.01)
            return None

        async def _collect_passive_window(timeout: float) -> None:
            deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)

        async def _send_request(
            target_id: int,
            command: int,
            args: list[int] | None = None,
            timeout: float = 0.7,
        ) -> dict | None:
            data = [target_id, command, 0, 0, 0, 0, 0, 0]
            if args:
                for idx, value in enumerate(args[:6]):
                    data[2 + idx] = int(value) & 0xFF
            await bridge.send_frame(can_v2_config_request_id(target_id), data, False, False)
            return await _wait_for_config_response(target_id, command, timeout)

        bridge = SlcanSerialBridge(
            port=port,
            baudrate=serial_baud,
            can_bitrate=can_bitrate,
            on_payload=_on_raw_payload,
        )
        await bridge.start()
        try:
            _drain_rx_events()
            steps = max(1, duration_s // max(1, interval_s))
            probe_max_id = 254 if deep_scan else 96
            for _ in range(steps):
                self._scan_stage_text = "Broadcast GET_SUMMARY"
                _notify_progress()
                await bridge.send_frame(
                    can_v2_config_request_id(0xFF), [0xFF, 3, 0, 0, 0, 0, 0, 0], False, False
                )
                await asyncio.sleep(0.05)
                await bridge.send_frame(
                    can_v2_config_request_id(0xFF), [0xFF, 3, 0, 0, 0, 0, 0, 0], False, False
                )
                self._scan_stage_text = "Listening for replies"
                _notify_progress()
                await _collect_passive_window(1.6)
                self._scan_stage_text = "Broadcast capability scan"
                _notify_progress()
                await bridge.send_frame(
                    can_v2_config_request_id(0xFF), [0xFF, 40, 0, 0, 0, 0, 0, 0], False, False
                )
                await asyncio.sleep(0.15)
                await bridge.send_frame(
                    can_v2_config_request_id(0xFF), [0xFF, 67, 0, 0, 0, 0, 0, 0], False, False
                )
                await asyncio.sleep(0.15)
                await _collect_passive_window(0.7)
                self._scan_stage_text = f"Probing IDs 1..{probe_max_id}"
                _notify_progress()
                for module_id in range(1, probe_max_id + 1):
                    resp = await _send_request(module_id, 3, timeout=0.12)
                    if resp and int(resp.get("status_code", 255)) == 0:
                        self._found_modules.add(module_id)
                        touched_ids.add(module_id)
                    await asyncio.sleep(0.01)
                self._scan_stage_text = "Querying discovered modules"
                _notify_progress()
                for module_id in sorted(self._found_modules):
                    await _send_request(module_id, 3, timeout=0.45)
                    await _send_request(module_id, 37, timeout=0.3)
                    await _send_request(module_id, 24, timeout=0.3)
                    await _send_request(module_id, 40, timeout=0.7)
                    await _send_request(module_id, 67, timeout=0.6)
                    await asyncio.sleep(0.08)
                await _collect_passive_window(0.5)
                await asyncio.sleep(interval_s)
            self._scan_stage_text = "Final metadata sync"
            _notify_progress()
            for module_id in sorted(self._found_modules):
                for cmd in (3, 37, 24, 40, 67):
                    await _send_request(module_id, cmd, timeout=0.45 if cmd == 3 else 0.3)
            await asyncio.sleep(2.0)
            self._scan_results_text = self._format_modules_log()
            if not self._found_modules:
                self._scan_diag_text = (
                    "No modules detected. "
                    f"Frames: received={received_frames}, info_701={info_frames}, summary_711={summary_frames}."
                )
            else:
                self._scan_diag_text = (
                    f"Diagnostics: modules={len(self._found_modules)}, "
                    f"received={received_frames}, info_701={info_frames}, summary_711={summary_frames}."
                )
            self._scan_stage_text = "Completed"
            _notify_progress()
        finally:
            persistent_notification.async_dismiss(self.hass, self._scan_notification_id)
            await bridge.stop()

    def _format_modules_log(self) -> str:
        if not self._found_modules:
            return "No modules found yet."
        lines: list[str] = []
        for module_id in sorted(self._found_modules):
            name = self._module_names.get(module_id)
            if name:
                lines.append(f"- Module {module_id}: {name}")
            else:
                lines.append(f"- Module {module_id}")
        return "\n".join(lines)

    def _calc_scan_remaining_seconds(self) -> int:
        if self._scan_started_monotonic is None or self._scan_duration_s <= 0:
            return 0
        elapsed = int(asyncio.get_running_loop().time() - self._scan_started_monotonic)
        return max(0, self._scan_duration_s - elapsed)
