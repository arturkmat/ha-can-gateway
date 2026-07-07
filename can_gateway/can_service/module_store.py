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
DEFAULT_ENTITIES_PATH = Path("/data/entities.json")
FALLBACK_STORE_PATH = Path(__file__).resolve().parent / "data" / "modules.json"
FALLBACK_ENTITIES_PATH = Path(__file__).resolve().parent / "data" / "entities.json"


def _store_path() -> Path:
    if DEFAULT_STORE_PATH.parent.exists():
        return DEFAULT_STORE_PATH
    FALLBACK_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return FALLBACK_STORE_PATH


def _entities_path() -> Path:
    if DEFAULT_ENTITIES_PATH.parent.exists():
        return DEFAULT_ENTITIES_PATH
    FALLBACK_ENTITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    return FALLBACK_ENTITIES_PATH


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _empty_store() -> dict[str, Any]:
    return {
        "version": STORE_VERSION,
        "updated_at": None,
        "last_scan_at": None,
        "discovery_version": 0,
        "modules": {},
    }


def _empty_entities_store() -> dict[str, Any]:
    return {
        "version": STORE_VERSION,
        "updated_at": None,
        "last_scan_at": None,
        "discovery_version": 0,
        "entity_count": 0,
        "entities": [],
    }


def load_store() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning("Could not read module store %s: %s", path, err)
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    modules = raw.get("modules")
    if not isinstance(modules, dict):
        raw["modules"] = {}
    raw.setdefault("version", STORE_VERSION)
    raw.setdefault("discovery_version", 0)
    return raw


def load_entities_store() -> dict[str, Any]:
    path = _entities_path()
    if not path.is_file():
        return _empty_entities_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning("Could not read entity catalog %s: %s", path, err)
        return _empty_entities_store()
    if not isinstance(raw, dict):
        return _empty_entities_store()
    entities = raw.get("entities")
    if not isinstance(entities, list):
        raw["entities"] = []
    raw.setdefault("version", STORE_VERSION)
    raw.setdefault("discovery_version", 0)
    raw.setdefault("entity_count", len(raw.get("entities") or []))
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


def load_entities() -> list[dict[str, Any]]:
    store = load_entities_store()
    rows = store.get("entities")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def save_discovery(
    modules: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    *,
    last_scan_at: float | None = None,
) -> int:
    """Persist modules + entity catalog atomically; returns new discovery_version."""
    keyed: dict[str, dict[str, Any]] = {}
    for mod in modules:
        if not isinstance(mod, dict):
            continue
        mid = mod.get("module_id")
        if not isinstance(mid, int) or not (1 <= mid <= 254):
            continue
        keyed[str(mid)] = mod

    catalog = [row for row in entities if isinstance(row, dict) and row.get("unique_id")]
    now = time.time()
    store = load_store()
    prev_version = int(store.get("discovery_version") or 0)
    discovery_version = prev_version + 1
    scan_ts = last_scan_at if last_scan_at is not None else now

    modules_payload = {
        "version": STORE_VERSION,
        "updated_at": now,
        "last_scan_at": scan_ts,
        "discovery_version": discovery_version,
        "modules": keyed,
    }
    entities_payload = {
        "version": STORE_VERSION,
        "updated_at": now,
        "last_scan_at": scan_ts,
        "discovery_version": discovery_version,
        "entity_count": len(catalog),
        "entities": catalog,
    }

    try:
        _atomic_write_json(_store_path(), modules_payload)
        _atomic_write_json(_entities_path(), entities_payload)
    except OSError as err:
        _LOGGER.error("Could not write discovery store: %s", err)
        return prev_version

    return discovery_version


def save_modules(
    modules: list[dict[str, Any]],
    *,
    last_scan_at: float | None = None,
    entities: list[dict[str, Any]] | None = None,
) -> int:
    """Backward-compatible wrapper; prefer save_discovery()."""
    if entities is None:
        entities = []
    return save_discovery(modules, entities, last_scan_at=last_scan_at)


def entity_counts_by_module(entities: list[dict[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for row in entities:
        if not isinstance(row, dict):
            continue
        mid = row.get("module_id")
        if isinstance(mid, int):
            counts[mid] = counts.get(mid, 0) + 1
    return counts


def discovery_snapshot(*, scan_status: str | None = None) -> dict[str, Any]:
    store = load_store()
    entities_store = load_entities_store()
    modules = load_modules()
    entities = load_entities()
    entity_counts = entity_counts_by_module(entities)
    modules_with_counts: list[dict[str, Any]] = []
    for mod in modules:
        row = dict(mod)
        mid = row.get("module_id")
        if isinstance(mid, int):
            row["entity_count"] = entity_counts.get(mid, 0)
        modules_with_counts.append(row)

    discovery_version = int(store.get("discovery_version") or entities_store.get("discovery_version") or 0)
    return {
        "ok": True,
        "module_count": len(modules),
        "entity_count": len(entities),
        "modules": modules_with_counts,
        "entities": entities,
        "updated_at": store.get("updated_at"),
        "last_scan_at": store.get("last_scan_at"),
        "discovery_version": discovery_version,
        "scan_status": scan_status,
    }
