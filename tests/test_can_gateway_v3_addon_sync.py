"""Tests for can_gateway_v3 add-on sync helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "can_gateway_v3"


def _mock_homeassistant() -> None:
    ha = types.ModuleType("homeassistant")
    ha_core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D101
        pass

    ha_core.HomeAssistant = HomeAssistant
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:  # noqa: D101
        def __init__(self, hass, logger, name, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval

        def __class_getitem__(cls, item):  # noqa: N805
            return cls

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    helpers.update_coordinator = update_coordinator

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator


def _ensure_package(name: str, path: Path) -> types.ModuleType:
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(path)]
    sys.modules[name] = pkg
    return pkg


def _load_in_package(module_name: str, file_path: Path, package: str):
    full_name = f"{package}.{module_name}"
    spec = importlib.util.spec_from_file_location(full_name, file_path, submodule_search_locations=[])
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package
    assert spec.loader is not None
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_seed_coordinator_from_addon_modules():
    _mock_homeassistant()
    _ensure_package("custom_components", COMP.parent)
    _ensure_package("custom_components.can_gateway_v3", COMP)
    const = _load_in_package("const", COMP / "const.py", "custom_components.can_gateway_v3")
    led_protocol = _load_in_package(
        "led_protocol", COMP / "led_protocol.py", "custom_components.can_gateway_v3"
    )
    protocol = _load_in_package("protocol", COMP / "protocol.py", "custom_components.can_gateway_v3")
    coordinator_mod = _load_in_package(
        "coordinator", COMP / "coordinator.py", "custom_components.can_gateway_v3"
    )
    addon_sync = _load_in_package(
        "addon_sync", COMP / "addon_sync.py", "custom_components.can_gateway_v3"
    )

    del led_protocol, protocol  # loaded as dependencies for coordinator

    coordinator = coordinator_mod.CanGatewayCoordinator(
        SimpleNamespace(loop=SimpleNamespace(is_running=lambda: False))
    )

    modules = [
        {
            "module_id": 5,
            "name": "Kuchnia",
            "hw_type": 2,
            "hw_name": "ESP32-C6",
            "mac": "11:22:33:44:55:66",
            "button_count": 2,
            "relay_count": 4,
            "shutter_count": 0,
            "runtime": {
                "relay_gpio_map": {"1": 7, "2": 8},
                "relays": [{"relay_no": 1, "on": True, "source": "local"}],
            },
        }
    ]
    addon_sync.seed_coordinator_from_modules(coordinator, modules)

    assert 5 in coordinator.scanned_modules
    info = coordinator.get_module_info(5)
    assert info.name == "Kuchnia"
    assert info.relay_gpio_map[1] == 7
    assert "m5_online" in coordinator.entity_descriptions
    assert const.DOMAIN == "can_gateway_v3"
