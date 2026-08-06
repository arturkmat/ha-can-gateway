from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from .bus_manager import BusManager
from .gpio_service import (
    clear_all_gpio_roles,
    clear_gpio_role,
    get_ota_info,
    list_pinout_profiles,
    module_pinout_payload,
    read_gpio_roles,
    read_gpio_values,
    set_button_timing,
    set_gpio_role,
    set_relay_pulse_ms,
    read_relay_pulse_ms,
)
from .mapping_service import read_all_mappings
from .mapping_write_service import clear_mappings, send_mappings
from .module_service import get_module_name, identify_module, set_module_id_by_mac, set_module_name
from .options import load_options
from .ota_upload_service import upload_firmware
from .sensor_scan_service import scan_1wire, scan_i2c, scan_mcp23017, scan_sensors
from .shutter_config_service import clear_shutter, get_shutter_config, set_shutter_relays, set_shutter_times
from .tab_load_service import load_module_tab

_LOGGER = logging.getLogger(__name__)


def _resolve_static_dir() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "static", here.parent / "static"):
        if (candidate / "index.html").is_file():
            return candidate
    return here / "static"


STATIC_DIR = _resolve_static_dir()


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

    async def api_entities(_request: web.Request) -> web.Response:
        return web.json_response(bus.entities_catalog(live_values=True))

    async def api_modules(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "modules": bus.list_modules(include_runtime=True),
                "module_count": len(bus.list_modules()),
                "status": bus.status(),
            }
        )

    async def api_discovery(_request: web.Request) -> web.Response:
        return web.json_response(bus.discovery_payload())

    async def api_can_send(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            can_id = int(body.get("can_id", 0))
            data = body.get("data") or []
            if not isinstance(data, list):
                return _json_error("data must be a list")
            ok = await asyncio.to_thread(bus.send_raw, can_id, [int(b) & 0xFF for b in data[:8]])
            status = 200 if ok else 503
            return web.json_response({"ok": ok, "can_id": can_id}, status=status)
        except (KeyError, TypeError, ValueError):
            return _json_error("invalid can_id/data")

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
        try:
            result = await asyncio.to_thread(bus.discovery_scan)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("api_scan error: %s", err, exc_info=True)
            return web.json_response({"ok": False, "error": str(err)}, status=503)
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
        _LOGGER.info(
            "Shutter command module=%s shutter=%s command=%s param=%s",
            mid,
            shutter_no,
            command,
            param,
        )
        result = await asyncio.to_thread(bus.set_shutter_command, mid, shutter_no, command, param)
        if not result.get("ok"):
            _LOGGER.warning(
                "Shutter command failed module=%s shutter=%s: %s",
                mid,
                shutter_no,
                result.get("error", result),
            )
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

    async def api_module_ota_upload(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        reader = await request.multipart()
        firmware = None
        async for part in reader:
            if part.name == "firmware":
                firmware = await part.read(decode=False)
                break
        if not firmware:
            return _json_error("missing firmware file")
        result = await asyncio.to_thread(upload_firmware, bus, mid, firmware)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_mappings_get(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(read_all_mappings, bus, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_mappings_post(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        body = await request.json()
        rows = body.get("mappings") or body.get("rows") or []
        result = await asyncio.to_thread(send_mappings, bus, mid, rows)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_mappings_delete(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(clear_mappings, bus, mid)
        return web.json_response(result)

    async def api_module_name_put(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        body = await request.json()
        result = await asyncio.to_thread(set_module_name, bus, mid, str(body.get("name", "")))
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_name_get(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        result = await asyncio.to_thread(get_module_name, bus, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_module_identify(request: web.Request) -> web.Response:
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
        result = await asyncio.to_thread(identify_module, bus, mid, int(body.get("seconds", 5)))
        return web.json_response(result)

    async def api_set_module_id(request: web.Request) -> web.Response:
        body = await request.json()
        result = await asyncio.to_thread(
            set_module_id_by_mac,
            bus,
            str(body.get("mac", "")),
            int(body.get("module_id", 0)),
        )
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_shutter_config_get(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            sid = int(request.match_info["shutter_no"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        result = await asyncio.to_thread(get_shutter_config, bus, mid, sid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_shutter_config_put(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            sid = int(request.match_info["shutter_no"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        body = await request.json()
        if body.get("clear"):
            result = await asyncio.to_thread(clear_shutter, bus, mid, sid)
        else:
            ro = int(body.get("relay_open", 0))
            rc = int(body.get("relay_close", 0))
            result = await asyncio.to_thread(set_shutter_relays, bus, mid, sid, ro, rc)
            if result.get("ok") and ("time_open_s" in body or "time_close_s" in body):
                times = await asyncio.to_thread(
                    set_shutter_times,
                    bus,
                    mid,
                    sid,
                    time_open_s=body.get("time_open_s"),
                    time_close_s=body.get("time_close_s"),
                )
                result["times"] = times
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_scan_sensor(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            kind = str(request.match_info["kind"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        fn = {
            "1wire": scan_1wire,
            "i2c": scan_i2c,
            "sensors": scan_sensors,
            "mcp23017": scan_mcp23017,
        }.get(kind)
        if fn is None:
            return _json_error("unknown scan kind")
        result = await asyncio.to_thread(fn, bus, mid)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_button_timing_put(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
        except (KeyError, ValueError):
            return _json_error("invalid module_id")
        body = await request.json()
        result = await asyncio.to_thread(
            set_button_timing,
            bus,
            mid,
            int(body.get("multiclick_ms", 400)),
            int(body.get("longpress_ms", 800)),
        )
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def api_gpio_clear_one(request: web.Request) -> web.Response:
        try:
            mid = int(request.match_info["module_id"])
            gpio = int(request.match_info["gpio"])
        except (KeyError, ValueError):
            return _json_error("invalid path")
        result = await asyncio.to_thread(clear_gpio_role, bus, mid, gpio)
        status = 200 if result.get("ok") else 503
        return web.json_response(result, status=status)

    async def index(_request: web.Request) -> web.Response:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            _LOGGER.error("Ingress UI missing index.html (STATIC_DIR=%s)", STATIC_DIR)
            return web.Response(text="CAN Gateway — brak index.html", content_type="text/plain", status=503)
        resp = web.FileResponse(index_path)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    app.router.add_get("/", index)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/entities", api_entities)
    app.router.add_get("/api/modules", api_modules)
    app.router.add_get("/api/discovery", api_discovery)
    app.router.add_post("/api/can/send", api_can_send)
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
    app.router.add_post("/api/modules/{module_id}/ota/upload", api_module_ota_upload)
    app.router.add_get("/api/modules/{module_id}/mappings", api_module_mappings_get)
    app.router.add_post("/api/modules/{module_id}/mappings", api_module_mappings_post)
    app.router.add_delete("/api/modules/{module_id}/mappings", api_module_mappings_delete)
    app.router.add_get("/api/modules/{module_id}/name", api_module_name_get)
    app.router.add_put("/api/modules/{module_id}/name", api_module_name_put)
    app.router.add_post("/api/modules/{module_id}/identify", api_module_identify)
    app.router.add_post("/api/modules/set-id-by-mac", api_set_module_id)
    app.router.add_get("/api/modules/{module_id}/shutters/{shutter_no}/config", api_shutter_config_get)
    app.router.add_put("/api/modules/{module_id}/shutters/{shutter_no}/config", api_shutter_config_put)
    app.router.add_post("/api/modules/{module_id}/scan/{kind}", api_scan_sensor)
    app.router.add_put("/api/modules/{module_id}/button-timing", api_button_timing_put)
    app.router.add_delete("/api/modules/{module_id}/gpio/{gpio}", api_gpio_clear_one)
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

    background: list[asyncio.Task] = []
    if not STATIC_DIR.joinpath("index.html").is_file():
        _LOGGER.warning("Panel Ingress: brak %s/index.html", STATIC_DIR)
    else:
        _LOGGER.info("Panel Ingress: static files from %s", STATIC_DIR)

    if bus.bus_ok:
        _LOGGER.info("V3 plain CAN — skan startowy")
        background.append(asyncio.create_task(_initial_scan(bus)))
    if options.auto_scan:
        _LOGGER.info("V3 plain CAN — auto_scan co %ds", options.auto_scan_interval_s)
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
