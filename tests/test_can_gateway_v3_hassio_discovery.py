"""Tests for can_gateway_v3 Supervisor (HassIO) discovery helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "can_gateway_v3"


def _load_slug_matcher():
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = Exception
    aiohttp.ClientTimeout = object
    sys.modules.setdefault("aiohttp", aiohttp)

    pkg = types.ModuleType("custom_components.can_gateway_v3")
    pkg.__path__ = [str(COMP)]
    sys.modules["custom_components.can_gateway_v3"] = pkg

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.can_gateway_v3.const",
        COMP / "const.py",
    )
    const_mod = importlib.util.module_from_spec(const_spec)
    const_mod.__package__ = "custom_components.can_gateway_v3"
    assert const_spec.loader is not None
    sys.modules["custom_components.can_gateway_v3.const"] = const_mod
    const_spec.loader.exec_module(const_mod)

    client_spec = importlib.util.spec_from_file_location(
        "custom_components.can_gateway_v3.addon_client",
        COMP / "addon_client.py",
    )
    client_mod = importlib.util.module_from_spec(client_spec)
    client_mod.__package__ = "custom_components.can_gateway_v3"
    assert client_spec.loader is not None
    sys.modules["custom_components.can_gateway_v3.addon_client"] = client_mod
    client_spec.loader.exec_module(client_mod)
    return client_mod, const_mod


def test_addon_slug_matches_can_gateway_variants():
    mod, _const = _load_slug_matcher()
    matches = mod.addon_slug_matches

    assert matches("can_gateway", "can_gateway")
    assert matches("local_can_gateway", "can_gateway")
    assert matches("a1b2c3d4_can_gateway", "can_gateway")
    assert not matches("other_addon", "can_gateway")
    assert not matches("", "can_gateway")


def test_build_addon_entry_data_shape():
    _mod, const = _load_slug_matcher()

    class _FlowStub:
        _addon_base_url = "http://can-gateway:8099"

        @staticmethod
        def _build_addon_entry_data(modules, *, scan_on_setup=False):
            return {
                const.CONF_CONNECTION_MODE: const.CONNECTION_MODE_ADDON,
                const.CONF_ADDON_SLUG: const.DEFAULT_ADDON_SLUG,
                const.CONF_ADDON_API_URL: _FlowStub._addon_base_url,
                const.CONF_SCAN_ON_SETUP: bool(scan_on_setup),
                const.CONF_INITIAL_SCAN_DONE: not bool(scan_on_setup),
                const.CONF_DISCOVERED_MODULES: modules,
            }

    modules = [{"module_id": 3, "name": "Salon"}]
    data = _FlowStub._build_addon_entry_data(modules, scan_on_setup=False)

    assert data[const.CONF_CONNECTION_MODE] == const.CONNECTION_MODE_ADDON
    assert data[const.CONF_ADDON_SLUG] == "can_gateway"
    assert data[const.CONF_DISCOVERED_MODULES] == modules
    assert data[const.CONF_INITIAL_SCAN_DONE] is True
    assert data[const.CONF_SCAN_ON_SETUP] is False
