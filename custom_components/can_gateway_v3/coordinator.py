from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    EVENT_BUTTON,
    EVENT_CONFIG_RESPONSE,
    EVENT_DEVICE_INFO,
    EVENT_DIAG,
    EVENT_GPIO,
    EVENT_RELAY,
    EVENT_RELAY_GPIO_MAP,
    EVENT_RELAY_MCP23017,
    EVENT_SENSOR,
    EVENT_SHUTTER_COMMAND,
    EVENT_SHUTTER,
    MCP23017_RELAY_CAN_BASE,
    MCP23017_RELAY_ENTITY_BASE,
)
from .led_protocol import LED_EFFECT_OFF, LED_STRIP_GPIO_DISABLED, LED_STRIP_TYPE_CCT, LED_STRIP_TYPE_RGB

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
    """Event-driven coordinator — CAN telemetry pushes state, no periodic polling."""

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
        self._module_name_buffers: dict[int, dict[str, Any]] = {}
        self._pending_led_strip_index: dict[int, int] = {}
        self._pending_relay_link_index: dict[int, int] = {}
        self._pending_relay_bind_route_index: dict[int, int] = {}
        self._pending_led_binding_index: dict[int, int] = {}

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

    def update_from_event(self, event_type: str, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        if module_id is not None:
            mid = int(module_id)
            if 1 <= mid <= 254:
                # Passive onboarding: if any valid module traffic appears, allow entity creation.
                self.scanned_modules.add(mid)
                self._touch_module_presence(mid)

        if event_type == EVENT_SENSOR:
            self._update_sensor(payload)
        elif event_type == EVENT_BUTTON:
            self._update_button(payload)
        elif event_type == EVENT_GPIO:
            self._update_gpio(payload)
        elif event_type == EVENT_RELAY:
            self._update_relays(payload, source="local")
        elif event_type == EVENT_RELAY_MCP23017:
            self._update_relays(payload, source="mcp23017")
        elif event_type == EVENT_RELAY_GPIO_MAP:
            self._update_relay_gpio_map(payload)
        elif event_type == EVENT_SHUTTER:
            self._update_shutter(payload)
        elif event_type == EVENT_SHUTTER_COMMAND:
            self._update_shutter_command(payload)
        elif event_type == EVENT_DEVICE_INFO:
            self._update_device_info(payload)
        elif event_type == EVENT_DIAG:
            self._update_diag(payload)
        elif event_type == EVENT_CONFIG_RESPONSE:
            self._update_config_response(payload)

    def _is_module_scanned(self, module_id: int) -> bool:
        return module_id in self.scanned_modules

    def _touch_module_presence(self, module_id: int) -> None:
        uid = f"m{module_id}_online"
        self._ensure_entity(
            EntityDescription(
                platform="binary_sensor",
                unique_id=uid,
                name=f"CAN M{module_id} Online",
                module_id=module_id,
                device_class="connectivity",
                icon="mdi:lan-connect",
            )
        )
        self._set_state(uid, True, {"module_id": module_id, "presence": "seen_bus_traffic"})

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

    def _update_sensor(self, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        sensor_no = payload.get("sensor_no")
        sensor_type = payload.get("sensor_type")
        if module_id is None or sensor_no is None or sensor_type is None:
            return
        module_id = int(module_id)
        if not self._is_module_scanned(module_id):
            return

        common_attrs = {"module_id": module_id, "sensor_no": sensor_no, "sensor_type": sensor_type}
        prefix = f"m{module_id}_s{sensor_no}_{sensor_type}"

        if "temperature_c" in payload:
            uid = f"{prefix}_temp"
            self._ensure_entity(
                EntityDescription(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} {sensor_type} {sensor_no} Temperature",
                    module_id=int(module_id),
                    device_class="temperature",
                    unit="°C",
                )
            )
            self._set_state(uid, payload["temperature_c"], common_attrs)

        if "humidity_pct" in payload:
            uid = f"{prefix}_humidity"
            self._ensure_entity(
                EntityDescription(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} {sensor_type} {sensor_no} Humidity",
                    module_id=int(module_id),
                    device_class="humidity",
                    unit="%",
                )
            )
            self._set_state(uid, payload["humidity_pct"], common_attrs)

        if "pressure_pa" in payload:
            uid = f"{prefix}_pressure"
            self._ensure_entity(
                EntityDescription(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} {sensor_type} {sensor_no} Pressure",
                    module_id=int(module_id),
                    device_class="pressure",
                    unit="Pa",
                )
            )
            self._set_state(uid, payload["pressure_pa"], common_attrs)

    def _update_button(self, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        button_no = payload.get("button_no")
        if module_id is None or button_no is None:
            return
        module_id = int(module_id)
        button_no = int(button_no)
        if not self._is_module_scanned(module_id):
            return
        uid = f"m{module_id}_btn{button_no}_action"
        self._ensure_entity(
            EntityDescription(
                platform="sensor",
                unique_id=uid,
                name=f"CAN M{module_id} Button {button_no} Action",
                module_id=module_id,
                icon="mdi:gesture-tap-button",
            )
        )
        self._set_state(
            uid,
            str(payload.get("action", "unknown")),
            {
                "module_id": module_id,
                "button_no": button_no,
                "action_code": payload.get("action_code"),
                "gpio_no": None,
            },
        )
        btn_bin_uid = f"m{module_id}_btn{button_no}_pressed"
        self._ensure_entity(
            EntityDescription(
                platform="binary_sensor",
                unique_id=btn_bin_uid,
                name=f"CAN M{module_id} Button {button_no}",
                module_id=module_id,
                device_class="running",
                icon="mdi:gesture-tap-button",
            )
        )
        self._set_state(
            btn_bin_uid,
            True,
            {
                "module_id": module_id,
                "button_no": button_no,
                "action": payload.get("action"),
                "action_code": payload.get("action_code"),
            },
        )

    def _update_gpio(self, payload: dict[str, Any]) -> None:
        if payload.get("valid") != 1:
            return
        module_id = payload.get("module_id")
        gpio = payload.get("gpio")
        if module_id is None or gpio is None:
            return
        module_id = int(module_id)
        if not self._is_module_scanned(module_id):
            return
        uid = f"m{module_id}_gpio{gpio}_binary"
        self._ensure_entity(
            EntityDescription(
                platform="binary_sensor",
                unique_id=uid,
                name=f"CAN M{module_id} GPIO {gpio}",
                module_id=int(module_id),
            )
        )
        self._set_state(
            uid,
            bool(payload.get("logical", 0)),
            {
                "module_id": module_id,
                "gpio": gpio,
                "raw": payload.get("raw"),
                "role": payload.get("role"),
                "index": payload.get("index"),
            },
        )

    def _update_relays(self, payload: dict[str, Any], source: str) -> None:
        module_id = payload.get("module_id")
        relays = payload.get("relays")
        if module_id is None or not isinstance(relays, list):
            return
        module_id = int(module_id)
        if not self._is_module_scanned(module_id):
            return
        info = self.get_module_info(module_id)
        max_relays = info.relay_count
        shutter_relays_in_use = info.shutter_reserved_can_relays()
        for relay in relays:
            raw_relay_no = relay.get("relay_no")
            if raw_relay_no is None:
                continue
            relay_no = self.normalize_shutter_relay_no(int(raw_relay_no))
            payload_entity_no = relay.get("relay_entity_no")
            payload_entity_can = (
                self.normalize_shutter_relay_no(int(payload_entity_no))
                if payload_entity_no is not None
                else relay_no
            )
            if relay_no in shutter_relays_in_use or payload_entity_can in shutter_relays_in_use:
                continue
            if source == "local":
                # Frame 0x600 (firmware_uniwersalne): [module_id, lo, hi, hc595_regs...]
                # — indeks HC595 kontynuuje się po 16 (lo+hi jako bank lokalny 1..16).
                if isinstance(max_relays, int) and max_relays > 0:
                    # `relay_count` (assigned_relays z GET_SUMMARY) obejmuje też HC595 (+ MCP),
                    # więc NIE przycinamy kanałów >16 wartością relay_count — tylko lokalny bank 1..16.
                    if relay_no <= 16 and relay_no > max_relays:
                        continue
            # Require explicit GPIO mapping for local bank 1..16 on all modules.
            # This avoids ghost relays created from raw 0x600 bitmap noise/placeholder bits.
            if source == "local" and relay_no <= 16:
                gpio = info.relay_gpio_map.get(relay_no)
                if gpio is None or gpio == 255:
                    continue
            if source == "mcp23017":
                chip_offset = payload.get("chip_offset")
                if not isinstance(chip_offset, int):
                    continue
                pins_ok = info.mcp_relay_pins_by_chip.get(int(chip_offset))
                if pins_ok is None:
                    # Nie znamy jeszcze ról pinów — ramka 0x602 dla wszystkich linii wygląda jak relaye (widma).
                    continue
                local_pin = relay.get("local_pin")
                if local_pin is None:
                    local_pin = relay_no - (MCP23017_RELAY_CAN_BASE + int(chip_offset) * 16)
                local_pin = int(local_pin)
                if local_pin not in pins_ok:
                    continue
                chip_suffix = f"_chip{int(chip_offset)}"
                uid = f"m{module_id}_mcp{chip_suffix}_relay{relay_no}"
                relay_entity_no = (
                    int(payload_entity_no) if payload_entity_no is not None else relay_no
                )
            else:
                relay_entity_no = relay_no
                relay_kind = "hc595" if relay_no > 16 else "local"
                uid = f"m{module_id}_{relay_kind}_relay{relay_no}"
            self._ensure_entity(
                EntityDescription(
                    platform="switch",
                    unique_id=uid,
                    name=(
                        f"CAN M{module_id} MCP Relay {relay_no}"
                        if source == "mcp23017"
                        else (
                            f"CAN M{module_id} HC595 Relay {relay_no}"
                            if relay_no > 16
                            else f"CAN M{module_id} Relay {relay_no}"
                        )
                    ),
                    module_id=int(module_id),
                    icon="mdi:light-switch",
                )
            )
            self._set_state(
                uid,
                str(relay.get("state", "")).upper() == "ON",
                {
                    "module_id": module_id,
                    "relay_no": relay_no,
                    "relay_entity_no": relay_entity_no,
                    "gpio_no": (
                        info.relay_gpio_map.get(relay_no)
                        if info.relay_gpio_map.get(relay_no) is not None
                        else relay.get("local_pin")
                    ),
                    "source": (
                        "mcp23017"
                        if source == "mcp23017"
                        else ("hc595" if relay_no > 16 else "local")
                    ),
                    "source_stream": source,
                    "chip_offset": payload.get("chip_offset"),
                    "i2c_address": payload.get("i2c_address"),
                    "local_pin": relay.get("local_pin"),
                },
            )

    def _update_relay_gpio_map(self, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        relay_map = payload.get("relay_map")
        if module_id is None or not isinstance(relay_map, list):
            return
        info = self.get_module_info(int(module_id))
        for item in relay_map:
            if not isinstance(item, dict):
                continue
            relay_no = item.get("relay_no")
            gpio = item.get("gpio")
            if relay_no is None or gpio is None:
                continue
            info.relay_gpio_map[int(relay_no)] = int(gpio)

    def _update_shutter(self, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        shutter_no = payload.get("shutter_no")
        if module_id is None or shutter_no is None:
            return
        module_id = int(module_id)
        if not self._is_module_scanned(module_id):
            return
        uid = f"m{module_id}_shutter{shutter_no}"
        self._ensure_entity(
            EntityDescription(
                platform="cover",
                unique_id=uid,
                name=f"CAN M{module_id} Shutter {shutter_no}",
                module_id=int(module_id),
                device_class="shutter",
            )
        )
        self._set_state(
            uid,
            {
                "position": payload.get("position"),
                "direction": payload.get("direction"),
                "direction_text": payload.get("direction_text"),
            },
            self._shutter_attrs(module_id, int(shutter_no)),
        )

    def _update_shutter_command(self, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        shutter_no = payload.get("shutter_no")
        if module_id is None or shutter_no is None:
            return
        module_id = int(module_id)
        if not self._is_module_scanned(module_id):
            return
        uid = f"m{module_id}_shutter{shutter_no}"
        self._ensure_entity(
            EntityDescription(
                platform="cover",
                unique_id=uid,
                name=f"CAN M{module_id} Shutter {shutter_no}",
                module_id=int(module_id),
                device_class="shutter",
            )
        )
        self._set_state(
            uid,
            self.get_state(uid).value if self.get_state(uid) is not None else None,
            self._shutter_attrs(module_id, int(shutter_no)),
        )

    def _update_device_info(self, payload: dict[str, Any]) -> None:
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

    def _update_diag(self, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        if module_id is None:
            return
        info = self.get_module_info(int(module_id))
        fw_major = payload.get("fw_major")
        fw_minor = payload.get("fw_minor")
        if fw_major is not None and fw_minor is not None:
            info.fw_version = f"{fw_major}.{fw_minor}"
        year = payload.get("build_year")
        month = payload.get("build_month")
        day = payload.get("build_day")
        hour = payload.get("build_hour")
        minute = payload.get("build_minute")
        if None not in (year, month, day, hour, minute):
            info.firmware_build_datetime = (
                f"{int(year):04d}.{int(month):02d}.{int(day):02d} "
                f"{int(hour):02d}:{int(minute):02d}"
            )
        uid = f"m{module_id}_diag_fw"
        self._ensure_entity(
            EntityDescription(
                platform="sensor",
                unique_id=uid,
                name=f"CAN M{module_id} Firmware",
                module_id=int(module_id),
                icon="mdi:chip",
            )
        )
        self._set_state(
            uid,
            info.fw_version or f"{fw_major}.{fw_minor}",
            {
                "module_id": module_id,
                "fw_major": fw_major,
                "fw_minor": fw_minor,
                "build_datetime": info.firmware_build_datetime,
            },
        )

    def _assemble_module_name_chunk(
        self, module_id: int, info: ModuleInfo, decoded: dict[str, Any]
    ) -> None:
        from .protocol import MODULE_NAME_CHUNK_READ, MODULE_NAME_MAX_LEN

        total_len = decoded.get("total_len")
        offset = decoded.get("offset")
        if total_len is None or offset is None:
            return
        total = int(total_len)
        off = int(offset)
        if total == 0:
            info.name = ""
            self._module_name_buffers.pop(module_id, None)
            return
        if total > MODULE_NAME_MAX_LEN or off < 0 or off >= MODULE_NAME_MAX_LEN:
            return
        buf = self._module_name_buffers.setdefault(module_id, {"total": total, "parts": {}})
        buf["total"] = total
        chunk = decoded.get("name_chunk")
        if isinstance(chunk, str):
            buf["parts"][off] = chunk
        parts: dict[int, str] = buf["parts"]
        expected = list(range(0, total, MODULE_NAME_CHUNK_READ))
        if not all(key in parts for key in expected):
            return
        assembled = "".join(parts[key] for key in sorted(parts.keys()))
        info.name = assembled[:total].strip()

    def _update_config_response(self, payload: dict[str, Any]) -> None:
        module_id = payload.get("module_id")
        command = payload.get("command")
        status_code = payload.get("status_code")
        decoded = payload.get("response_decoded")
        if module_id is None or command is None or not isinstance(decoded, dict):
            return
        module_id = int(module_id)
        info = self.get_module_info(module_id)
        cmd = int(command)
        status = int(status_code or 255)
        # Keep "scan-first" behavior, but accept full scan-flow responses
        # (some firmware variants may not return complete GET_SUMMARY quickly).
        if status == 0 and cmd in (3, 24, 37, 40, 50, 67, 70, 87, 88, 114, 115, 118, 119):
            self.scanned_modules.add(module_id)
            self._touch_module_presence(module_id)
        if cmd == 3:
            button_count = decoded.get("button_count")
            if button_count is not None:
                info.button_count = int(button_count)
                self._ensure_button_entities(module_id, info.button_count)
            relay_count = decoded.get("relay_count")
            if relay_count is not None:
                info.relay_count = int(relay_count)
            shutter_count = decoded.get("shutter_count")
            if shutter_count is not None:
                info.shutter_count = int(shutter_count)
                self._ensure_shutter_entities(module_id, info.shutter_count)
        if cmd == 37:
            self._assemble_module_name_chunk(module_id, info, decoded)
        elif cmd == 50:
            if status != 0:
                return
            shutter_no = decoded.get("shutter_no")
            relay_open = decoded.get("relay_open")
            relay_close = decoded.get("relay_close")
            if None not in (shutter_no, relay_open, relay_close):
                ro = self.normalize_shutter_relay_no(int(relay_open))
                rc = self.normalize_shutter_relay_no(int(relay_close))
                sid = int(shutter_no)
                # 0 = brak przypisania (0xFF w NVS). Trzymaj slot jeśli jest choć jeden przekaźnik,
                # inaczej MCP/HC595 pokażą switch nawet gdy jedna strona pary nie jest ustawiona.
                if ro > 0 or rc > 0:
                    info.shutter_relay_map[sid] = (ro, rc)
                else:
                    info.shutter_relay_map.pop(sid, None)
                # Encje switch mogły powstać przy 0x600 zanim mapa przyszła — usuń z HA/registry runtime.
                self.prune_switches_mapped_to_any_shutter(module_id)
        elif cmd == 70:
            if status != 0:
                return
            chip_o = decoded.get("chip_offset")
            packed = decoded.get("roles_packed")
            if chip_o is None or not isinstance(packed, list) or len(packed) < 4:
                return
            ci = int(chip_o)
            relay_pins = CanGatewayCoordinator.parse_mcp_role_dump_packed(
                [int(packed[i]) & 0xFF for i in range(4)]
            )
            prev = info.mcp_relay_pins_by_chip.get(ci)
            info.mcp_relay_pins_by_chip[ci] = relay_pins
            if prev is not None:
                dropped = prev - relay_pins
                if dropped:
                    self.discard_switch_entities_for_relays(
                        module_id,
                        [
                            MCP23017_RELAY_CAN_BASE + ci * 16 + int(lp)
                            for lp in dropped
                        ],
                    )
        elif cmd == 24:
            year = decoded.get("build_year")
            month = decoded.get("build_month")
            day = decoded.get("build_day")
            hour = decoded.get("build_hour")
            minute = decoded.get("build_minute")
            if None not in (year, month, day, hour, minute):
                info.firmware_build_datetime = (
                    f"{int(year):04d}.{int(month):02d}.{int(day):02d} "
                    f"{int(hour):02d}:{int(minute):02d}"
                )
        elif cmd == 110:
            if status != 0:
                return
            strip_idx = self._pending_led_strip_index.pop(module_id, None)
            if strip_idx is None:
                return
            self._apply_led_strip_config(module_id, strip_idx, decoded)
        elif cmd == 87:
            if status != 0:
                return
            info.relay_bind_routes.clear()
            count = decoded.get("route_count")
            if isinstance(count, int) and count == 0:
                self._refresh_relay_bind_route_entities(module_id)
        elif cmd == 88:
            if status != 0:
                return
            route_idx = self._pending_relay_bind_route_index.pop(module_id, None)
            if route_idx is None:
                return
            self._apply_relay_bind_route(module_id, int(route_idx), decoded)
        elif cmd == 114:
            if status != 0:
                return
            info.led_bindings.clear()
            count = decoded.get("binding_count")
            if isinstance(count, int) and count == 0:
                self._refresh_led_binding_entities(module_id)
        elif cmd == 115:
            if status != 0:
                return
            bind_idx = self._pending_led_binding_index.pop(module_id, None)
            if bind_idx is None:
                return
            self._apply_led_binding(module_id, int(bind_idx), decoded)
        elif cmd == 118:
            if status != 0:
                return
            info.relay_links.clear()
            count = decoded.get("link_count")
            if isinstance(count, int) and count == 0:
                self._refresh_relay_link_entities(module_id)
        elif cmd == 119:
            if status != 0:
                return
            link_idx = self._pending_relay_link_index.pop(module_id, None)
            if link_idx is None:
                return
            self._apply_relay_link(module_id, int(link_idx), decoded)

    def note_relay_link_query(self, module_id: int, link_index: int) -> None:
        self._pending_relay_link_index[int(module_id)] = int(link_index)

    def note_relay_bind_route_query(self, module_id: int, route_index: int) -> None:
        self._pending_relay_bind_route_index[int(module_id)] = int(route_index)

    def note_led_binding_query(self, module_id: int, binding_index: int) -> None:
        self._pending_led_binding_index[int(module_id)] = int(binding_index)

    def _apply_relay_link(self, module_id: int, link_index: int, decoded: dict[str, Any]) -> None:
        src = decoded.get("src_relay")
        trigger = decoded.get("trigger")
        tgt_mod = decoded.get("target_module")
        tgt_rly = decoded.get("target_relay")
        tgt_state = decoded.get("target_state")
        if None in (src, trigger, tgt_mod, tgt_rly, tgt_state):
            return
        from .protocol import format_relay_link_summary

        info = self.get_module_info(module_id)
        entry = {
            "index": link_index,
            "src_relay": int(src),
            "trigger": int(trigger),
            "target_module": int(tgt_mod),
            "target_relay": int(tgt_rly),
            "target_state": int(tgt_state),
        }
        entry["summary"] = format_relay_link_summary(
            entry["src_relay"],
            entry["trigger"],
            entry["target_module"],
            entry["target_relay"],
            entry["target_state"],
        )
        info.relay_links[int(link_index)] = entry
        self._refresh_relay_link_entities(module_id)

    def _apply_relay_bind_route(
        self, module_id: int, route_index: int, decoded: dict[str, Any]
    ) -> None:
        button = decoded.get("button")
        action = decoded.get("action")
        tgt_mod = decoded.get("target_module")
        relay = decoded.get("relay")
        relay_state = decoded.get("relay_state")
        if None in (button, action, tgt_mod, relay, relay_state):
            return
        from .protocol import format_relay_bind_route_summary

        info = self.get_module_info(module_id)
        entry = {
            "index": route_index,
            "button": int(button),
            "action": int(action),
            "target_module": int(tgt_mod),
            "relay": int(relay),
            "relay_state": int(relay_state),
        }
        entry["summary"] = format_relay_bind_route_summary(
            entry["button"],
            entry["action"],
            entry["target_module"],
            entry["relay"],
            entry["relay_state"],
        )
        info.relay_bind_routes[int(route_index)] = entry
        self._refresh_relay_bind_route_entities(module_id)

    def _refresh_relay_bind_route_entities(self, module_id: int) -> None:
        info = self.get_module_info(module_id)
        active = set(info.relay_bind_routes.keys())
        prefix = f"m{module_id}_relay_bind_route_"
        for uid in list(self.entity_descriptions.keys()):
            if uid.startswith(prefix) and uid != f"m{module_id}_relay_bind_routes_summary":
                idx_str = uid[len(prefix) :]
                if idx_str.isdigit() and int(idx_str) not in active:
                    del self.entity_descriptions[uid]
                    self.entity_states.pop(uid, None)
        for idx, entry in sorted(info.relay_bind_routes.items()):
            uid = f"m{module_id}_relay_bind_route_{idx}"
            self._ensure_entity(
                EntityDescription(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} Relay Bind Route {idx + 1}",
                    module_id=module_id,
                    icon="mdi:transit-connection-variant",
                )
            )
            self._set_state(
                uid,
                entry["summary"],
                {
                    "module_id": module_id,
                    "route_index": idx,
                    **{k: v for k, v in entry.items() if k != "summary"},
                },
            )
        summary_uid = f"m{module_id}_relay_bind_routes_summary"
        self._ensure_entity(
            EntityDescription(
                platform="sensor",
                unique_id=summary_uid,
                name=f"CAN M{module_id} Relay Bind Routes",
                module_id=module_id,
                icon="mdi:transit-connection-variant",
            )
        )
        self._set_state(
            summary_uid,
            len(info.relay_bind_routes),
            {
                "module_id": module_id,
                "routes": [
                    info.relay_bind_routes[i] for i in sorted(info.relay_bind_routes.keys())
                ],
            },
        )

    def _refresh_relay_link_entities(self, module_id: int) -> None:
        info = self.get_module_info(module_id)
        active = set(info.relay_links.keys())
        prefix = f"m{module_id}_relay_link_"
        for uid in list(self.entity_descriptions.keys()):
            if uid.startswith(prefix) and uid != f"m{module_id}_relay_links_summary":
                idx_str = uid[len(prefix) :]
                if idx_str.isdigit() and int(idx_str) not in active:
                    del self.entity_descriptions[uid]
                    self.entity_states.pop(uid, None)
        for idx, entry in sorted(info.relay_links.items()):
            uid = f"m{module_id}_relay_link_{idx}"
            self._ensure_entity(
                EntityDescription(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} Relay Link {idx + 1}",
                    module_id=module_id,
                    icon="mdi:link-variant",
                )
            )
            self._set_state(
                uid,
                entry["summary"],
                {
                    "module_id": module_id,
                    "link_index": idx,
                    **{k: v for k, v in entry.items() if k != "summary"},
                },
            )
        summary_uid = f"m{module_id}_relay_links_summary"
        self._ensure_entity(
            EntityDescription(
                platform="sensor",
                unique_id=summary_uid,
                name=f"CAN M{module_id} Relay Links",
                module_id=module_id,
                icon="mdi:link-variant",
            )
        )
        self._set_state(
            summary_uid,
            len(info.relay_links),
            {
                "module_id": module_id,
                "links": [info.relay_links[i] for i in sorted(info.relay_links.keys())],
            },
        )

    def _apply_led_binding(self, module_id: int, binding_index: int, decoded: dict[str, Any]) -> None:
        strip_index = decoded.get("strip_index")
        source_module = decoded.get("source_module")
        button = decoded.get("button")
        action = decoded.get("action")
        effect_id = decoded.get("effect_id")
        duration_s = decoded.get("duration_s")
        if None in (strip_index, source_module, button, action, effect_id, duration_s):
            return
        from .led_protocol import LED_STRIP_TYPE_RGB, format_led_binding_summary

        info = self.get_module_info(module_id)
        strip_type = LED_STRIP_TYPE_RGB
        strip_cfg = info.led_strips.get(int(strip_index))
        if isinstance(strip_cfg, dict):
            strip_type = int(strip_cfg.get("strip_type", LED_STRIP_TYPE_RGB))
        color_byte = int(decoded.get("color_byte", 0))
        entry = {
            "index": binding_index,
            "strip_index": int(strip_index),
            "source_module": int(source_module),
            "button": int(button),
            "action": int(action),
            "effect_id": int(effect_id),
            "duration_s": int(duration_s),
            "color_byte": color_byte,
            "strip_type": strip_type,
        }
        entry["summary"] = format_led_binding_summary(
            strip_index=entry["strip_index"],
            source_module=entry["source_module"],
            button=entry["button"],
            action=entry["action"],
            effect_id=entry["effect_id"],
            duration_s=entry["duration_s"],
            strip_type=strip_type,
            color_byte=color_byte,
        )
        info.led_bindings[int(binding_index)] = entry
        self._refresh_led_binding_entities(module_id)

    def _refresh_led_binding_entities(self, module_id: int) -> None:
        info = self.get_module_info(module_id)
        active = set(info.led_bindings.keys())
        prefix = f"m{module_id}_led_binding_"
        for uid in list(self.entity_descriptions.keys()):
            if uid.startswith(prefix) and uid != f"m{module_id}_led_bindings_summary":
                idx_str = uid[len(prefix) :]
                if idx_str.isdigit() and int(idx_str) not in active:
                    del self.entity_descriptions[uid]
                    self.entity_states.pop(uid, None)
        for idx, entry in sorted(info.led_bindings.items()):
            uid = f"m{module_id}_led_binding_{idx}"
            self._ensure_entity(
                EntityDescription(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} LED Binding {idx + 1}",
                    module_id=module_id,
                    icon="mdi:led-strip-variant",
                )
            )
            self._set_state(
                uid,
                entry["summary"],
                {
                    "module_id": module_id,
                    "binding_index": idx,
                    **{k: v for k, v in entry.items() if k != "summary"},
                },
            )
        summary_uid = f"m{module_id}_led_bindings_summary"
        self._ensure_entity(
            EntityDescription(
                platform="sensor",
                unique_id=summary_uid,
                name=f"CAN M{module_id} LED Bindings",
                module_id=module_id,
                icon="mdi:led-strip-variant",
            )
        )
        self._set_state(
            summary_uid,
            len(info.led_bindings),
            {
                "module_id": module_id,
                "bindings": [info.led_bindings[i] for i in sorted(info.led_bindings.keys())],
            },
        )

    def _ensure_shutter_entities(self, module_id: int, shutter_count: int | None) -> None:
        if not isinstance(shutter_count, int) or shutter_count <= 0:
            return
        count = min(28, shutter_count)
        for shutter_no in range(1, count + 1):
            uid = f"m{module_id}_shutter{shutter_no}"
            self._ensure_entity(
                EntityDescription(
                    platform="cover",
                    unique_id=uid,
                    name=f"CAN M{module_id} Shutter {shutter_no}",
                    module_id=module_id,
                    device_class="shutter",
                )
            )

    def _ensure_button_entities(self, module_id: int, button_count: int | None) -> None:
        if not isinstance(button_count, int) or button_count <= 0:
            return
        count = min(64, button_count)
        for button_no in range(1, count + 1):
            uid = f"m{module_id}_btn{button_no}_action"
            self._ensure_entity(
                EntityDescription(
                    platform="sensor",
                    unique_id=uid,
                    name=f"CAN M{module_id} Button {button_no} Action",
                    module_id=module_id,
                    icon="mdi:gesture-tap-button",
                )
            )

    def _shutter_attrs(self, module_id: int, shutter_no: int) -> dict[str, Any]:
        info = self.get_module_info(module_id)
        relay_open, relay_close = info.shutter_relay_map.get(shutter_no, (None, None))
        return {
            "module_id": module_id,
            "shutter_no": shutter_no,
            "relay_open_no": relay_open,
            "relay_close_no": relay_close,
            "gpio_open_no": info.relay_gpio_map.get(relay_open) if isinstance(relay_open, int) else None,
            "gpio_close_no": info.relay_gpio_map.get(relay_close) if isinstance(relay_close, int) else None,
            "gpio_no": None,
        }

    def note_led_strip_query(self, module_id: int, strip_index: int) -> None:
        self._pending_led_strip_index[int(module_id)] = int(strip_index)

    def _apply_led_strip_config(self, module_id: int, strip_index: int, decoded: dict[str, Any]) -> None:
        gpio = decoded.get("gpio")
        count = decoded.get("count")
        if gpio is None or count is None:
            return
        gpio = int(gpio)
        count = int(count)
        info = self.get_module_info(module_id)
        if gpio == LED_STRIP_GPIO_DISABLED or count <= 0:
            info.led_strips.pop(strip_index, None)
            uid = f"m{module_id}_led_strip{strip_index}"
            if uid in self.entity_descriptions:
                del self.entity_descriptions[uid]
            self.entity_states.pop(uid, None)
            return
        strip_type = int(decoded.get("strip_type", LED_STRIP_TYPE_RGB))
        cfg = {
            "strip_index": strip_index,
            "gpio": gpio,
            "count": count,
            "strip_type": strip_type,
            "brightness": int(decoded.get("brightness", 128)),
            "idle_effect": int(decoded.get("idle_effect", LED_EFFECT_OFF)),
            "r": int(decoded.get("r", 0)),
            "g": int(decoded.get("g", 0)),
            "b": int(decoded.get("b", 0)),
            "kelvin": decoded.get("kelvin"),
        }
        info.led_strips[strip_index] = cfg
        uid = f"m{module_id}_led_strip{strip_index}"
        type_label = "CCT" if strip_type == LED_STRIP_TYPE_CCT else "RGB"
        self._ensure_entity(
            EntityDescription(
                platform="light",
                unique_id=uid,
                name=f"CAN M{module_id} LED Strip {strip_index} ({type_label})",
                module_id=module_id,
                icon="mdi:led-strip-variant",
            )
        )
        is_on = cfg["idle_effect"] != LED_EFFECT_OFF
        self._set_state(
            uid,
            {
                "is_on": is_on,
                "brightness": cfg["brightness"],
                "effect": cfg["idle_effect"],
                "r": cfg["r"],
                "g": cfg["g"],
                "b": cfg["b"],
                "kelvin": cfg.get("kelvin"),
            },
            {
                "module_id": module_id,
                "strip_index": strip_index,
                "gpio": gpio,
                "pixel_count": count,
                "strip_type": strip_type,
            },
        )


    def clear_all_entities(self) -> None:
        """Purge all cached entities/states/metadata (call before reload)."""
        self.entity_descriptions.clear()
        self.entity_states.clear()
        self.module_info.clear()
        if hasattr(self, 'scanned_modules'):
            self.scanned_modules.clear()
        for listeners_list in self.platform_adders.values():
            listeners_list.clear()
        if hasattr(self, 'state_listeners'):
            self.state_listeners.clear()
        if hasattr(self, 'switch_prune_listeners'):
            self.switch_prune_listeners.clear()
        _LOGGER.info("Coordinator cleared all cached entities")

# Backward alias for entity helpers
GatewayRuntime = CanGatewayCoordinator

