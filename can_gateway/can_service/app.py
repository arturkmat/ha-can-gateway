from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from .bus_manager import BusManager
from .gpio_service import (
    clear_all_gpio_roles,
    get_ota_info,
    list_pinout_profiles,
    module_pinout_payload,
    read_gpio_roles,
    read_gpio_values,
    set_gpio_role,
    set_relay_pulse_ms,
    read_relay_pulse_ms,
)
from .mqtt_bridge import MqttBridge
from .options import load_options
from .tab_load_service import load_module_tab

_LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def create_app(bus: BusManager) -> web.Application:
    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def api_status(_request: web.Request) -> web.Response:
        return web.json_response(bus.status())

    async def api_state(_request: web.Request) -> web.Response:
        return web.json_response(bus.full_state())

    async def api_modules(_request: web.Request) -> web.Response:
        return web.json_response({"modules": bus.list_modules()})

    async def api_module_detail(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        detail = bus.module_detail(mid)
        if detail is None:
            return _json_error("module not found", 404)
        return web.json_response(detail)

    async def api_scan(_request: web.Request) -> web.Response:
        result = await asyncio.to_thread(bus.discovery_scan)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_refresh(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(bus.refresh_module, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_reboot(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(bus.reboot_module, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_tab_load(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
        tab = str(body.get("tab", "modules"))
        result = await asyncio.to_thread(load_module_tab, bus, mid, tab)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_relay_set(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            relay_no = int(request.match_info["relay_no"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
        state = str(body.get("state", "toggle"))
        result = await asyncio.to_thread(bus.set_relay_state, mid, relay_no, state)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_shutter_command(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            shutter_no = int(request.match_info["shutter_no"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
        command = str(body.get("command", "stop"))
        param = int(body.get("param", 0))
        result = await asyncio.to_thread(bus.set_shutter_command, mid, shutter_no, command, param)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_pinouts(_request: web.Request) -> web.Response:
        return web.json_response(list_pinout_profiles())

    async def api_module_pinout(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(module_pinout_payload, bus, mid)
        status = 200 if result.get("ok") else 404
        return web.json_response(result, status=status)

    async def api_gpio_roles_read(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(read_gpio_roles, bus, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_gpio_values_read(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(read_gpio_values, bus, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_gpio_role_set(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            gpio = int(request.match_info["gpio"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
        result = await asyncio.to_thread(
            set_gpio_role,
            bus,
            mid,
            gpio,
            role=body.get("role", "Unused"),
            index=int(body.get("index", 0)),
            flags=int(body.get("flags", 0)),
        )
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_gpio_clear(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(clear_all_gpio_roles, bus, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_relay_pulse_get(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            relay_no = int(request.match_info["relay_no"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        result = await asyncio.to_thread(read_relay_pulse_ms, bus, mid, relay_no)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_relay_pulse_set(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            relay_no = int(request.match_info["relay_no"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
        pulse_ms = int(body.get("pulse_ms", 0))
        result = await asyncio.to_thread(set_relay_pulse_ms, bus, mid, relay_no, pulse_ms)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_ota(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(get_ota_info, bus, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def index(_request: web.Request) -> web.Response:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            return web.Response(text="CAN Gateway — brak index.html", content_type="text/plain")
        return web.FileResponse(index_path)

    app.router.add_get("/", index)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/modules", api_modules)
    app.router.add_get("/api/modules/{module_id}", api_module_detail)
    app.router.add_post("/api/scan", api_scan)
    app.router.add_post("/api/modules/{module_id}/tab-load", api_module_tab_load)
    app.router.add_post("/api/modules/{module_id}/refresh", api_module_refresh)
    app.router.add_post("/api/modules/{module_id}/reboot", api_module_reboot)
    app.router.add_post("/api/modules/{module_id}/relays/{relay_no}", api_relay_set)
    app.router.add_get("/api/modules/{module_id}/relays/{relay_no}/pulse", api_relay_pulse_get)
    app.router.add_post("/api/modules/{module_id}/relays/{relay_no}/pulse", api_relay_pulse_set)
    app.router.add_post("/api/modules/{module_id}/shutters/{shutter_no}", api_shutter_command)
    app.router.add_get("/api/pinouts", api_pinouts)
    app.router.add_get("/api/modules/{module_id}/pinout", api_module_pinout)
    app.router.add_post("/api/modules/{module_id}/gpio/read", api_gpio_roles_read)
    app.router.add_post("/api/modules/{module_id}/gpio/values", api_gpio_values_read)
    app.router.add_post("/api/modules/{module_id}/gpio/clear", api_gpio_clear)
    app.router.add_post("/api/modules/{module_id}/gpio/{gpio}", api_gpio_role_set)
    app.router.add_get("/api/modules/{module_id}/ota", api_module_ota)
    app.router.add_static("/static", STATIC_DIR, show_index=False)
    return app


async def run_server(
    bus: BusManager,
    host: str = "0.0.0.0",
    port: int = 8099,
    shutdown: asyncio.Event | None = None,
) -> None:
    options = load_options()
    app = create_app(bus)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    _LOGGER.info("HTTP listening on %s:%d (Ingress port)", host, port)

    mqtt = MqttBridge(bus, options)
    await mqtt.start()

    background: list[asyncio.Task] = []
    if bus.bus_ok:
        background.append(asyncio.create_task(_initial_scan(bus)))
    if options.auto_scan:
        background.append(asyncio.create_task(_auto_scan_loop(bus, options.auto_scan_interval_s)))
    background.append(asyncio.create_task(_relay_telemetry_loop(bus)))

    try:
        if shutdown is not None:
            await shutdown.wait()
        else:
            while True:
                await asyncio.sleep(3600)
    finally:
        for task in background:
            task.cancel()
        for task in background:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await mqtt.stop()
        await runner.cleanup()


async def _initial_scan(bus: BusManager) -> None:
    await asyncio.sleep(0.5)
    _LOGGER.info("Running initial discovery scan (konfigurator F5)...")
    try:
        result = await asyncio.to_thread(bus.discovery_scan)
        if not result.get("ok"):
            _LOGGER.warning("Initial discovery scan failed: %s", result.get("error"))
    except Exception:  # noqa: BLE001
        _LOGGER.error("Initial discovery scan error", exc_info=True)


async def _auto_scan_loop(bus: BusManager, interval_s: int) -> None:
    await asyncio.sleep(max(5, interval_s))
    while True:
        try:
            if bus.bus_ok:
                await asyncio.to_thread(bus.auto_scan_broadcast)
                await asyncio.to_thread(bus.refresh_relay_telemetry, 0.6)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("auto-scan broadcast failed", exc_info=True)
        await asyncio.sleep(max(5, interval_s))


async def _relay_telemetry_loop(bus: BusManager) -> None:
    """Co ~20 s nasłuch broadcastów 0x600/0x602 (firmware co 30 s)."""
    await asyncio.sleep(8)
    while True:
        try:
            if bus.bus_ok:
                await asyncio.to_thread(bus.refresh_relay_telemetry, 0.75)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("relay telemetry refresh failed", exc_info=True)
        await asyncio.sleep(20)
