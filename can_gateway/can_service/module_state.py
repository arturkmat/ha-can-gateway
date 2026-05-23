"""Cache stanów modułów (relay, rolety, sensory) — telemetria CAN."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from protocol_constants import MCP23017_RELAY_CAN_BASE


@dataclass
class RelayState:
    relay_no: int
    on: bool = False
    pulse_ms: int = 0
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "relay_no": self.relay_no,
            "on": self.on,
            "pulse_ms": self.pulse_ms,
            "source": self.source,
        }


@dataclass
class ShutterState:
    shutter_no: int
    position: int | None = None
    direction: int = 0
    relay_open: int = 0
    relay_close: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "shutter_no": self.shutter_no,
            "position": self.position,
            "direction": self.direction,
            "direction_text": {0: "stopped", 1: "opening", 2: "closing"}.get(self.direction, "unknown"),
            "relay_open": self.relay_open,
            "relay_close": self.relay_close,
        }


@dataclass
class ModuleRuntimeState:
    relays: dict[int, RelayState] = field(default_factory=dict)
    shutters: dict[int, ShutterState] = field(default_factory=dict)
    shutter_map: dict[int, tuple[int, int]] = field(default_factory=dict)
    relay_pulse_ms: dict[int, int] = field(default_factory=dict)
    mcp_relay_pins: dict[int, set[int]] = field(default_factory=dict)
    relay_gpio_map: dict[int, int] = field(default_factory=dict)
    sensors: list[dict[str, Any]] = field(default_factory=list)
    hw_flags: int = 0
    gpio_roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    gpio_values: dict[str, dict[str, Any]] = field(default_factory=dict)

    def relay_list(self) -> list[dict[str, Any]]:
        return [self.relays[k].to_dict() for k in sorted(self.relays.keys())]

    def shutter_list(self) -> list[dict[str, Any]]:
        return [self.shutters[k].to_dict() for k in sorted(self.shutters.keys())]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relays": self.relay_list(),
            "shutters": self.shutter_list(),
            "shutter_map": {str(k): list(v) for k, v in self.shutter_map.items()},
            "relay_gpio_map": {str(k): v for k, v in self.relay_gpio_map.items()},
            "hw_flags": self.hw_flags,
            "sensors": list(self.sensors),
            "gpio_roles": dict(self.gpio_roles),
            "gpio_values": dict(self.gpio_values),
        }


def decode_relays_0x600(data: list[int]) -> list[tuple[int, bool]]:
    if len(data) < 2:
        return []
    out: list[tuple[int, bool]] = []
    relay_no = 1
    for byte in data[1:]:
        for bit in range(8):
            out.append((relay_no, bool((byte >> bit) & 0x01)))
            relay_no += 1
    return out


def decode_mcp_relays_0x602(data: list[int]) -> list[tuple[int, bool]]:
    if len(data) < 4:
        return []
    chip_offset = int(data[1])
    gpa = int(data[2])
    gpb = int(data[3])
    base = MCP23017_RELAY_CAN_BASE + chip_offset * 16
    out: list[tuple[int, bool]] = []
    for local_pin in range(16):
        on = ((gpa >> local_pin) & 1) if local_pin < 8 else ((gpb >> (local_pin - 8)) & 1)
        out.append((base + local_pin, bool(on)))
    return out


def parse_mcp_role_dump(packed: list[int]) -> set[int]:
    pins: set[int] = set()
    if len(packed) < 4:
        return pins
    for i in range(16):
        b = int(packed[i // 4]) & 0xFF
        role = (b >> ((i % 4) * 2)) & 0x03
        if role == 1:
            pins.add(i)
    return pins
