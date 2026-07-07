"""Tests for CAN Gateway add-on module persistence."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "can_gateway" / "can_service"
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))

import module_store as ms  # noqa: E402


def test_save_and_load_modules_roundtrip(tmp_path, monkeypatch):
    store_path = tmp_path / "modules.json"
    entities_path = tmp_path / "entities.json"
    monkeypatch.setattr(ms, "DEFAULT_STORE_PATH", store_path)
    monkeypatch.setattr(ms, "FALLBACK_STORE_PATH", store_path)
    monkeypatch.setattr(ms, "DEFAULT_ENTITIES_PATH", entities_path)
    monkeypatch.setattr(ms, "FALLBACK_ENTITIES_PATH", entities_path)

    sample = [
        {
            "module_id": 3,
            "name": "Salon",
            "hw_type": 2,
            "hw_name": "ESP32-C6",
            "mac": "AA:BB:CC:DD:EE:03",
            "firmware_build": "2026.07.05 12:00",
            "button_count": 4,
            "relay_count": 8,
            "shutter_count": 1,
            "runtime": {"relay_gpio_map": {"1": 5}},
        }
    ]
    entities = [
        {
            "platform": "switch",
            "unique_id": "m3_local_relay1",
            "name": "CAN M3 Relay 1",
            "module_id": 3,
            "value": False,
            "attributes": {"module_id": 3, "relay_no": 1},
        }
    ]
    version = ms.save_discovery(sample, entities, last_scan_at=123456.0)
    assert store_path.is_file()
    assert entities_path.is_file()
    assert version == 1

    loaded = ms.load_modules()
    assert len(loaded) == 1
    assert loaded[0]["module_id"] == 3
    assert loaded[0]["name"] == "Salon"
    assert loaded[0]["runtime"]["relay_gpio_map"]["1"] == 5

    catalog = ms.load_entities()
    assert len(catalog) == 1
    assert catalog[0]["unique_id"] == "m3_local_relay1"

    snapshot = ms.discovery_snapshot(scan_status="ok")
    assert snapshot["module_count"] == 1
    assert snapshot["entity_count"] == 1
    assert snapshot["scan_status"] == "ok"
    assert snapshot["last_scan_at"] == 123456.0
    assert snapshot["discovery_version"] == 1
    assert snapshot["modules"][0]["entity_count"] == 1


def test_load_store_empty_when_missing(tmp_path, monkeypatch):
    store_path = tmp_path / "missing.json"
    monkeypatch.setattr(ms, "DEFAULT_STORE_PATH", store_path)
    monkeypatch.setattr(ms, "FALLBACK_STORE_PATH", store_path)
    assert ms.load_modules() == []
    store = ms.load_store()
    assert store["modules"] == {}


def test_load_store_recovers_from_corrupt_json(tmp_path, monkeypatch):
    store_path = tmp_path / "modules.json"
    monkeypatch.setattr(ms, "DEFAULT_STORE_PATH", store_path)
    monkeypatch.setattr(ms, "FALLBACK_STORE_PATH", store_path)
    store_path.write_text("{not-json", encoding="utf-8")
    assert ms.load_modules() == []
