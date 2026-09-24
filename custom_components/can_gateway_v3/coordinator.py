from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import MCP23017_RELAY_CAN_BASE, MCP23017_RELAY_ENTITY_BASE

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class EntityDescription:
    platform: str
    unique_id: str
    name: str
    module_id: int
    device_class: str | None = None
    unit: str | None = None
    icon: str | None = None


@dataclass(slots=True)
class ModuleInfo:
    module_id: int
    name: str | None = None
    hw_type: int | None = None
    hw_name: str | None = None
    mac: str | None = None
    fw_version: str | None = None
    firmware_build_datetime: str | None = None
    relay_count: int | None = None
    button_count: int | None = None
    shutter_count: int | None = None
    relay_gpio_map: dict[int, int] = field(default_factory=dict)
    shutter_relay_map: dict[int, tuple[int, int]] = field(default_factory=dict)
    # MCP23017: chip_offset → zbiór local_pin (0–15), pin ma rolę RELAY wg cmd 70
    mcp_relay_pins_by_chip: dict[int, set[int]] = field(default_factory=dict)
    led_strips: dict[int, dict[str, Any]] = field(default_factory=dict)
    relay_links: dict[int, dict[str, Any]] = field(default_factory=dict)
    relay_bind_routes: dict[int, dict[str, Any]] = field(default_factory=dict)
    led_bindings: dict[int, dict[str, Any]] = field(default_factory=dict)

    def shutter_reserved_can_relays(self) -> set[int]:
        """CAN relay numbers used by any shutter (open and/or close side)."""
        out: set[int] = set()
        for ro, rc in self.shutter_relay_map.values():
            if isinstance(ro, int) and ro > 0:
                out.add(ro)
            if isinstance(rc, int) and rc > 0:
                out.add(rc)
        return out


@dataclass(slots=True)
class EntityState:
    value: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)


class CanGatewayCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator holding HA entities subordinated to the add-on catalog."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, _LOGGER, name="can_gateway_v3", update_interval=None)
        self.entity_descriptions: dict[str, EntityDescription] = {}
        self.entity_states: dict[str, EntityState] = {}
        self.module_info: dict[int, ModuleInfo] = {}
        self.scanned_modules: set[int] = set()
        self.platform_adders: dict[str, list[Callable[[list[EntityDescription]], None]]] = {
            "sensor": [],
            "binary_sensor": [],
            "switch": [],
            "cover": [],
            "light": [],
            "button": [],
        }
        self.state_listeners: list[Callable[[str], None]] = []
        self.switch_prune_listeners: list[Callable[[], None]] = []
        self.last_scan_started_at: str | None = None
        self.last_scan_finished_at: str | None = None
        self.last_scan_stage: str | None = None
        self.last_scan_status: str | None = None
        self.last_scan_modules: list[int] = []
        self.last_scan_details: str | None = None
        self.selected_module_id: int | None = None
        self._button_reset_handles: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "modules": sorted(self.scanned_modules),
            "entity_count": len(self.entity_descriptions),
        }

    def _schedule_refresh(self) -> None:
        if self.hass.loop.is_running():
            self.async_set_updated_data(self.snapshot())

    def register_platform_adder(
        self, platform: str, adder: Callable[[list[EntityDescription]], None]
    ) -> Callable[[], None]:
        self.platform_adders[platform].append(adder)
        existing = [d for d in self.entity_descriptions.values() if d.platform == platform]
        if existing:
            adder(existing)

        def _unsubscribe() -> None:
            if adder in self.platform_adders[platform]:
                self.platform_adders[platform].remove(adder)

        return _unsubscribe

    def register_state_listener(self, listener: Callable[[str], None]) -> Callable[[], None]:
        self.state_listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self.state_listeners:
                self.state_listeners.remove(listener)

        return _unsubscribe

    def register_switch_prune_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.switch_prune_listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self.switch_prune_listeners:
                self.switch_prune_listeners.remove(listener)

        return _unsubscribe

    def _notify_switch_prune_listeners(self) -> None:
        for fn in self.switch_prune_listeners:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def parse_mcp_role_dump_packed(packed_4_bytes: list[int]) -> set[int]:
        relay_pins: set[int] = set()
        if len(packed_4_bytes) < 4:
            return relay_pins
        for i in range(16):
            b = int(packed_4_bytes[i // 4]) & 0xFF
            role = (b >> ((i % 4) * 2)) & 0x03
            if role == 1:
                relay_pins.add(i)
        return relay_pins

    @staticmethod
    def normalize_shutter_relay_no(relay_no: int) -> int:
        """Normalize shutter-mapped relay number to CAN numbering."""
        rn = int(relay_no)
        if rn <= 0:
            return rn
        # Backward compatibility: some firmware/config snapshots report MCP in 101+ numbering.
        # Convert it to CAN numbering (57+) so switch filtering is consistent.
        mcp_span = 16 * 8
        if MCP23017_RELAY_ENTITY_BASE <= rn < (MCP23017_RELAY_ENTITY_BASE + mcp_span):
            return MCP23017_RELAY_CAN_BASE + (rn - MCP23017_RELAY_ENTITY_BASE)
        return rn

    @staticmethod
    def switch_uid_for_can_relay(module_id: int, relay_no: int) -> str | None:
        r = int(relay_no)
        if r <= 0:
            return None
        base = MCP23017_RELAY_CAN_BASE
        if r <= 16:
            return f"m{module_id}_local_relay{r}"
        if r < base:
            return f"m{module_id}_hc595_relay{r}"
        chip_off = (r - base) // 16
        return f"m{module_id}_mcp_chip{chip_off}_relay{r}"

    def discard_switch_entities_for_relays(self, module_id: int, relay_nums: Iterable[int]) -> None:
        changed = False
        mid = int(module_id)
        for rn in relay_nums:
            can_no = int(rn)
            uid = self.switch_uid_for_can_relay(mid, can_no)
            if uid is None:
                continue
            if uid in self.entity_descriptions:
                del self.entity_descriptions[uid]
                changed = True
            self.entity_states.pop(uid, None)
        if changed:
            self._notify_switch_prune_listeners()

    def prune_switches_mapped_to_any_shutter(self, module_id: int) -> None:
        info = self.get_module_info(module_id)
        self.discard_switch_entities_for_relays(
            module_id, sorted(info.shutter_reserved_can_relays())
        )

    def get_state(self, unique_id: str) -> EntityState | None:
        return self.entity_states.get(unique_id)

    def get_module_info(self, module_id: int) -> ModuleInfo:
        info = self.module_info.get(module_id)
        if info is None:
            info = ModuleInfo(module_id=module_id)
            self.module_info[module_id] = info
        return info

    def _update_device_info(self, payload: dict[str, Any]) -> None:
        """Apply module hardware metadata (no entity creation)."""
        module_id = payload.get("module_id")
        if module_id is None:
            return
        info = self.get_module_info(int(module_id))
        if "hw_type" in payload:
            info.hw_type = int(payload["hw_type"])
        if "hw_name" in payload:
            info.hw_name = str(payload["hw_name"])
        if "mac" in payload:
            info.mac = str(payload["mac"])

    def _ensure_entity(self, description: EntityDescription) -> None:
        if description.unique_id in self.entity_descriptions:
            return
        self.entity_descriptions[description.unique_id] = description
        adders = self.platform_adders.get(description.platform, [])
        if adders:
            for adder in adders:
                adder([description])

    def _set_state(self, unique_id: str, value: Any, attributes: dict[str, Any]) -> None:
        self.entity_states[unique_id] = EntityState(value=value, attributes=attributes)
        for listener in self.state_listeners:
            listener(unique_id)
        self._schedule_refresh()

    def notify_gateway_state(self) -> None:
        for listener in self.state_listeners:
            listener("__gateway_status__")

    def get_known_module_ids(self) -> list[int]:
        mids = {int(mid) for mid in self.scanned_modules}
        mids.update(int(mid) for mid in self.module_info.keys())
        return sorted(mid for mid in mids if 1 <= mid <= 254)

    def set_selected_module_id(self, module_id: int | None) -> None:
        if module_id is not None and not (1 <= int(module_id) <= 254):
            return
        self.selected_module_id = int(module_id) if module_id is not None else None
        for listener in self.state_listeners:
            listener("__gateway_selection__")

    def mark_scan_started(self, stage: str = "scan") -> None:
        self.last_scan_started_at = datetime.now().isoformat(timespec="seconds")
        self.last_scan_finished_at = None
        self.last_scan_stage = stage
        self.last_scan_status = "running"
        self.last_scan_modules = []
        self.last_scan_details = None
        self.notify_gateway_state()

    def mark_scan_finished(self, status: str, details: str | None = None) -> None:
        self.last_scan_finished_at = datetime.now().isoformat(timespec="seconds")
        self.last_scan_status = status
        self.last_scan_stage = "completed"
        self.last_scan_modules = sorted(self.scanned_modules)
        self.last_scan_details = details
        self.notify_gateway_state()

    def pulse_binary_sensor(
        self, unique_id: str, attributes: dict[str, Any] | None = None, duration: float = 0.75
    ) -> None:
        """Emit a short virtual pulse for a mapped binary sensor entity."""
        if unique_id not in self.entity_descriptions:
            return
        attrs = dict(attributes or {})
        previous_handle = self._button_reset_handles.pop(unique_id, None)
        if previous_handle is not None:
            previous_handle.cancel()
        self._set_state(unique_id, True, attrs)

        def _reset() -> None:
            self._button_reset_handles.pop(unique_id, None)
            self._set_state(unique_id, False, attrs)

        self._button_reset_handles[unique_id] = self.hass.loop.call_later(duration, _reset)

    def clear_all_entities(self) -> None:
        """Purge all cached entities/states/metadata (call before reload)."""
        for handle in self._button_reset_handles.values():
            handle.cancel()
        self._button_reset_handles.clear()
        self.entity_descriptions.clear()
        self.entity_states.clear()
        self.module_info.clear()
        if hasattr(self, "scanned_modules"):
            self.scanned_modules.clear()
        for listeners_list in self.platform_adders.values():
            listeners_list.clear()
        if hasattr(self, "state_listeners"):
            self.state_listeners.clear()
        if hasattr(self, "switch_prune_listeners"):
            self.switch_prune_listeners.clear()
        _LOGGER.info("Coordinator cleared all cached entities")


# Backward alias for entity helpers
GatewayRuntime = CanGatewayCoordinator
