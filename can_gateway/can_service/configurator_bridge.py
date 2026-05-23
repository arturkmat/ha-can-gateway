"""Mostek między BusManager a ConfiguratorEngine (wspólny silnik konfiguratora Windows)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from configurator_engine import ConfiguratorEngine
from protocol_constants import CAN_ID_CONFIG_REQUEST

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

    def bus_send(
        self, target_module_id: int, can_id: int, data: list[int], *, log_traffic: bool = True
    ) -> None:
        del log_traffic
        if can_id == CAN_ID_CONFIG_REQUEST:
            self._bus.send_config(int(data[0]) if data else target_module_id, int(data[1]), list(data[2:8]))
            return
        self._bus.send_raw(int(can_id), list(data))

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
