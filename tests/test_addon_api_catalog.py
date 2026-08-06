"""Tests for add-on API catalog gating (no phantom entity synthesis)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "can_gateway" / "can_service"
LIB = ROOT / "can_gateway" / "lib"
for path in (SERVICE, LIB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_bus_manager():
    pkg = types.ModuleType("can_service")
    pkg.__path__ = [str(SERVICE)]
    sys.modules["can_service"] = pkg

    for name in ("can_send", "configurator_bridge", "options"):
        spec = importlib.util.spec_from_file_location(
            f"can_service.{name}",
            SERVICE / f"{name}.py",
            submodule_search_locations=[str(SERVICE)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "can_service"
        assert spec.loader is not None
        sys.modules[f"can_service.{name}"] = mod
        spec.loader.exec_module(mod)

    spec = importlib.util.spec_from_file_location(
        "can_service.bus_manager",
        SERVICE / "bus_manager.py",
        submodule_search_locations=[str(SERVICE)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "can_service"
    assert spec.loader is not None
    sys.modules["can_service.bus_manager"] = mod
    spec.loader.exec_module(mod)
    return mod.BusManager, sys.modules["can_service.module_store"]


def _patch_store_paths(tmp_path, monkeypatch, store_mod) -> None:
    store_path = tmp_path / "modules.json"
    entities_path = tmp_path / "entities.json"
    monkeypatch.setattr(store_mod, "DEFAULT_STORE_PATH", store_path)
    monkeypatch.setattr(store_mod, "FALLBACK_STORE_PATH", store_path)
    monkeypatch.setattr(store_mod, "DEFAULT_ENTITIES_PATH", entities_path)
    monkeypatch.setattr(store_mod, "FALLBACK_ENTITIES_PATH", entities_path)


def test_entities_catalog_empty_without_persisted_catalog(tmp_path, monkeypatch):
    BusManager, store_mod = _load_bus_manager()
    _patch_store_paths(tmp_path, monkeypatch, store_mod)

    store_mod.save_discovery(
        [{"module_id": 2, "name": "Garage", "relay_count": 4}],
        [],
        last_scan_at=1000.0,
    )

    bus = MagicMock(spec=BusManager)
    bus.bus_ok = True
    bus._last_scan_status = "ok"
    bus._export_modules_for_catalog = MagicMock(
        return_value=[{"module_id": 2, "name": "Garage", "relay_count": 4}]
    )
    bus._build_entity_catalog = MagicMock(
        return_value=[
            {
                "platform": "switch",
                "unique_id": "m2_local_relay1",
                "name": "Relay 1",
                "module_id": 2,
                "value": False,
                "attributes": {},
            }
        ]
    )

    payload = BusManager.entities_catalog(bus, live_values=True)

    assert payload["discovery_version"] == 1
    assert payload["entity_count"] == 0
    assert payload["entities"] == []
    bus._build_entity_catalog.assert_not_called()


def test_entities_catalog_uses_live_module_detail_not_frozen_disk_snapshot(tmp_path, monkeypatch):
    """Regression: entities_catalog() must resolve each persisted module through
    module_detail() (live engine context) rather than using the disk snapshot's
    runtime verbatim, otherwise moving-cover position / relay state freezes at
    the value from the last explicit scan instead of updating live."""
    BusManager, store_mod = _load_bus_manager()
    _patch_store_paths(tmp_path, monkeypatch, store_mod)

    # Two modules persisted on disk with a stale/frozen shutter position (50).
    store_mod.save_discovery(
        [
            {"module_id": 2, "name": "Garage", "runtime": {"shutters": [{"shutter_no": 1, "position": 50}]}},
            {"module_id": 3, "name": "Kitchen", "runtime": {"shutters": [{"shutter_no": 1, "position": 50}]}},
        ],
        [
            {
                "platform": "cover",
                "unique_id": "m2_shutter1",
                "name": "Shutter 1",
                "module_id": 2,
                "value": {"position": 50, "direction": 0},
                "attributes": {"module_id": 2, "shutter_no": 1},
            }
        ],
        last_scan_at=1000.0,
    )

    bus = MagicMock(spec=BusManager)
    bus.bus_ok = True
    bus._last_scan_status = "ok"

    # module_detail() simulates the live engine: module 2 is mid-movement (position
    # jumped to 77 since the last disk snapshot), module 3 has no live context and
    # module_detail() falls back to returning the persisted dict unchanged.
    def _fake_module_detail(mid):
        if mid == 2:
            return {"module_id": 2, "name": "Garage", "runtime": {"shutters": [{"shutter_no": 1, "position": 77}]}}
        return None

    bus.module_detail = MagicMock(side_effect=_fake_module_detail)
    bus._build_entity_catalog = MagicMock(
        return_value=[
            {
                "platform": "cover",
                "unique_id": "m2_shutter1",
                "name": "Shutter 1",
                "module_id": 2,
                "value": {"position": 77, "direction": 0},
                "attributes": {"module_id": 2, "shutter_no": 1},
            }
        ]
    )

    BusManager.entities_catalog(bus, live_values=True)

    # module_detail() must have been consulted for every persisted module.
    called_ids = sorted(c.args[0] for c in bus.module_detail.call_args_list)
    assert called_ids == [2, 3]

    # _build_entity_catalog() must receive the LIVE module 2 (position 77 from
    # module_detail()), not the frozen disk snapshot (position 50). Module 3 has
    # no live context, so it must fall back to its persisted disk entry unchanged.
    modules_passed = bus._build_entity_catalog.call_args.args[0]
    mod2 = next(m for m in modules_passed if m["module_id"] == 2)
    mod3 = next(m for m in modules_passed if m["module_id"] == 3)
    assert mod2["runtime"]["shutters"][0]["position"] == 77
    assert mod3["runtime"]["shutters"][0]["position"] == 50


def test_discovery_payload_uses_persisted_entity_counts(tmp_path, monkeypatch):
    BusManager, store_mod = _load_bus_manager()
    _patch_store_paths(tmp_path, monkeypatch, store_mod)

    entities = [
        {
            "platform": "switch",
            "unique_id": "m5_local_relay1",
            "name": "Relay 1",
            "module_id": 5,
            "value": False,
            "attributes": {"module_id": 5, "relay_no": 1},
        }
    ]
    store_mod.save_discovery(
        [{"module_id": 5, "name": "Kitchen"}],
        entities,
        last_scan_at=2000.0,
    )

    bus = MagicMock(spec=BusManager)
    bus._last_scan_status = "ok"
    bus.list_modules = MagicMock(
        return_value=[{"module_id": 5, "name": "Kitchen", "hw_type": 2}]
    )

    payload = BusManager.discovery_payload(bus)

    assert payload["entity_count"] == 1
    assert payload["discovery_version"] == 1
    assert payload["modules"][0]["entity_count"] == 1
