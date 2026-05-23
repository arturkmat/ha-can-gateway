"""Lazy-load zakładek — delegacja do ConfiguratorEngine (ten sam kod co konfigurator Windows)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bus_manager import BusManager


def load_module_tab(bus: BusManager, module_id: int, tab: str) -> dict:
    if not bus.ensure_bus():
        return {"ok": False, "error": bus._bus_error or "bus not open"}  # noqa: SLF001
    mid = int(module_id)
    if not (1 <= mid <= 254):
        return {"ok": False, "error": "invalid module_id"}
    return bus._get_engine().run_tab_load(mid, tab)  # noqa: SLF001
