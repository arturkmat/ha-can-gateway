"""Mostek między BusManager a ConfiguratorEngine (wspólny silnik konfiguratora Windows)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from configurator_engine import ConfiguratorEngine
from protocol_constants import CAN_ID_CONFIG_REQUEST, UNKNOWN_MODULE_IDS

from .can_send import prepare_outgoing_frames
from .mapping_service import read_all_mappings

if TYPE_CHECKING:
    from .bus_manager import BusManager

_LOGGER = logging.getLogger(__name__)


class _BusIoAdapter:
    def __init__(self, bus: BusManager) -> None:
        self._bus = bus

    def recv(self, timeout: float) -> Any | None:
        return self._bus._recv(timeout)  # noqa: SLF001

    def prepare_outgoing_frames(
        self, target_module_id: int, can_id: int, data: list[int]
    ) -> list[tuple[int, list[int]]] | None:
        self._bus._ensure_transport_macs()  # noqa: SLF001
        return prepare_outgoing_frames(self._bus._transport, target_module_id, can_id, data)  # noqa: SLF001

    def send_can_frame(self, frame_id: int, data: list[int]) -> None:
        import can

        payload = [int(b) & 0xFF for b in data[:8]]
        if len(payload) < 8:
            payload.extend([0] * (8 - len(payload)))
        if not self._bus._send_message(  # noqa: SLF001
            can.Message(arbitration_id=int(frame_id), is_extended_id=True, data=payload)
        ):
            raise RuntimeError("CAN send failed")

    def bus_send(
        self, target_module_id: int, can_id: int, data: list[int], *, log_traffic: bool = True
    ) -> None:
        del log_traffic, target_module_id
        if can_id == CAN_ID_CONFIG_REQUEST:
            module_id = int(data[0]) if data else 0xFF
            command = int(data[1]) if len(data) > 1 else 0
            args = [int(b) for b in data[2:8]]
            frames = self.prepare_outgoing_frames(module_id, can_id, data)
            if not frames:
                raise RuntimeError(f"TX blocked CONFIG module={module_id} cmd=0x{command:02X}")
            for frame_id, payload in frames:
                self.send_can_frame(frame_id, payload)
            return
        frames = self.prepare_outgoing_frames(int(data[0]) if data else 0xFF, int(can_id), data)
        if not frames:
            raise RuntimeError(f"TX blocked can_id=0x{can_id:03X}")
        for frame_id, payload in frames:
            self.send_can_frame(frame_id, payload)

    def normalize(self, message: Any) -> Any | None:
        return self._bus._normalize_message(message)  # noqa: SLF001

    def io_acquire(self) -> None:
        self._bus._scan_lock.acquire()  # noqa: SLF001
        self._bus._rx_enabled.clear()  # noqa: SLF001

    def io_release(self) -> None:
        self._bus._rx_enabled.set()  # noqa: SLF001
        self._bus._scan_lock.release()  # noqa: SLF001

    def log(self, message: str) -> None:
        _LOGGER.debug("%s", message)

    def notify(self) -> None:
        self._bus._notify()  # noqa: SLF001

    def bus_ok(self) -> bool:
        return self._bus.ensure_bus()

    def invalidate_transport_macs(self) -> None:
        self._bus._invalidate_transport_macs()  # noqa: SLF001

    def sync_transport_macs(self) -> None:
        self._bus._sync_transport_macs_from_engine()  # noqa: SLF001


def _read_mappings(engine: ConfiguratorEngine, module_id: int) -> dict[str, Any]:
    from .bus_manager import BusManager

    bus = engine._io._bus  # noqa: SLF001
    assert isinstance(bus, BusManager)
    return read_all_mappings(bus, module_id)


def create_engine(bus: BusManager) -> ConfiguratorEngine:
    master = bus._options.master_key_bytes  # noqa: SLF001
    return ConfiguratorEngine(
        _BusIoAdapter(bus),
        master_key=master,
        read_mappings=_read_mappings,
    )
