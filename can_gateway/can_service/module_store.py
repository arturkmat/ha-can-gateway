"""Persistent storage for discovered CAN modules (Home Assistant add-on /data)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
DEFAULT_STORE_PATH = Path("/data/modules.json")
FALLBACK_STORE_PATH = Path(__file__).resolve().parent / "data" / "modules.json"


def _store_path() -> Path:
    if DEFAULT_STORE_PATH.parent.exists():
        return DEFAULT_STORE_PATH
    FALLBACK_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return FALLBACK_STORE_PATH


def load_store() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"version": STORE_VERSION, "updated_at": None, "last_scan_at": None, "modules": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning("Could not read module store %s: %s", path, err)
        return {"version": STORE_VERSION, "updated_at": None, "last_scan_at": None, "modules": {}}
    if not isinstance(raw, dict):
        return {"version": STORE_VERSION, "updated_at": None, "last_scan_at": None, "modules": {}}
    modules = raw.get("modules")
    if not isinstance(modules, dict):
        raw["modules"] = {}
    raw.setdefault("version", STORE_VERSION)
    return raw


def load_modules() -> list[dict[str, Any]]:
    store = load_store()
    modules_raw = store.get("modules")
    if not isinstance(modules_raw, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in sorted(modules_raw.keys(), key=lambda k: int(k) if str(k).isdigit() else k):
        mod = modules_raw.get(key)
        if isinstance(mod, dict) and isinstance(mod.get("module_id"), int):
            out.append(mod)
    return out


def save_modules(
    modules: list[dict[str, Any]],
    *,
    last_scan_at: float | None = None,
) -> None:
    path = _store_path()
    keyed: dict[str, dict[str, Any]] = {}
    for mod in modules:
        if not isinstance(mod, dict):
            continue
        mid = mod.get("module_id")
        if not isinstance(mid, int) or not (1 <= mid <= 254):
            continue
        keyed[str(mid)] = mod
    now = time.time()
    payload = {
        "version": STORE_VERSION,
        "updated_at": now,
        "last_scan_at": last_scan_at if last_scan_at is not None else now,
        "modules": keyed,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as err:
        _LOGGER.error("Could not write module store %s: %s", path, err)


def discovery_snapshot(*, scan_status: str | None = None) -> dict[str, Any]:
    store = load_store()
    modules = load_modules()
    return {
        "ok": True,
        "module_count": len(modules),
        "modules": modules,
        "updated_at": store.get("updated_at"),
        "last_scan_at": store.get("last_scan_at"),
        "scan_status": scan_status,
    }
