from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable

import serial

_LOGGER = logging.getLogger(__name__)

RX_BUFFER_MAX_BYTES = 64 * 1024

CanFrameSender = Callable[[int, list[int], bool, bool], Awaitable[None]]
RawPayloadCallback = Callable[[str], None]


def _slcan_speed_code(bitrate: int) -> str:
    mapping = {
        10000: "0",
        20000: "1",
        50000: "2",
        100000: "3",
        125000: "4",
        250000: "5",
        500000: "6",
        800000: "7",
        1000000: "8",
    }
    return mapping.get(int(bitrate), "5")


def _to_slcan_frame(can_id: int, data: list[int], ext: bool, rtr: bool) -> str:
    dlc = min(8, max(0, len(data)))
    payload = "".join(f"{b & 0xFF:02X}" for b in data[:dlc])
    if ext:
        kind = "R" if rtr else "T"
        return f"{kind}{can_id & 0x1FFFFFFF:08X}{dlc:X}{payload}\r"
    kind = "r" if rtr else "t"
    return f"{kind}{can_id & 0x7FF:03X}{dlc:X}{payload}\r"


def _from_slcan_line(line: str) -> str | None:
    if not line:
        return None
    kind = line[0]
    try:
        if kind in ("t", "r"):
            can_id = int(line[1:4], 16)
            dlc = int(line[4], 16)
            data_hex = line[5:5 + dlc * 2]
            data = [int(data_hex[i: i + 2], 16) for i in range(0, len(data_hex), 2)]
            frame = {"id": can_id, "ext": 0, "rtr": 1 if kind == "r" else 0, "dlc": dlc, "data": data}
            return json.dumps(frame)
        if kind in ("T", "R"):
            can_id = int(line[1:9], 16)
            dlc = int(line[9], 16)
            data_hex = line[10:10 + dlc * 2]
            data = [int(data_hex[i: i + 2], 16) for i in range(0, len(data_hex), 2)]
            frame = {"id": can_id, "ext": 1, "rtr": 1 if kind == "R" else 0, "dlc": dlc, "data": data}
            return json.dumps(frame)
    except Exception:  # noqa: BLE001
        return None
    return None


class SlcanSerialBridge:
    def __init__(
        self,
        port: str,
        baudrate: int,
        can_bitrate: int,
        on_payload: RawPayloadCallback,
    ) -> None:
        self._port = port
        self._baudrate = int(baudrate)
        self._can_bitrate = int(can_bitrate)
        self._on_payload = on_payload
        self._serial: serial.Serial | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._reconnecting = False
        self._rx_buffer = ""
        self._send_lock = asyncio.Lock()

    async def start(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self._open_and_init)
        self._task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await asyncio.get_running_loop().run_in_executor(None, self._close_serial)

    async def send_frame(self, can_id: int, data: list[int], ext: bool = False, rtr: bool = False) -> None:
        if self._serial is None:
            return
        wire = _to_slcan_frame(can_id, data, ext, rtr).encode("ascii")
        async with self._send_lock:
            await asyncio.get_running_loop().run_in_executor(None, self._serial.write, wire)

    def _open_and_init(self) -> None:
        self._serial = serial.Serial(self._port, self._baudrate, timeout=0.25)
        self._serial.write(b"C\r")
        self._serial.write(f"S{_slcan_speed_code(self._can_bitrate)}\r".encode("ascii"))
        self._serial.write(b"O\r")

    def _close_serial(self) -> None:
        try:
            if self._serial:
                self._serial.write(b"C\r")
                self._serial.close()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error during SLCAN close", exc_info=True)
        finally:
            self._serial = None

    async def _try_reconnect(self) -> None:
        if self._reconnecting or self._stopping:
            return
        self._reconnecting = True
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._close_serial)
            await asyncio.sleep(1.0)
            if self._stopping:
                return
            await asyncio.get_running_loop().run_in_executor(None, self._open_and_init)
            self._rx_buffer = ""
            _LOGGER.info("SLCAN serial reconnected on %s", self._port)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("SLCAN reconnect failed on %s", self._port, exc_info=True)
        finally:
            self._reconnecting = False

    async def _read_loop(self) -> None:
        while not self._stopping:
            try:
                if self._serial is None:
                    await asyncio.sleep(0.3)
                    continue
                chunk = await asyncio.get_running_loop().run_in_executor(None, self._serial.read, 512)
                if not chunk:
                    continue
                text = chunk.decode("ascii", errors="ignore")
                self._rx_buffer += text
                if len(self._rx_buffer) > RX_BUFFER_MAX_BYTES:
                    _LOGGER.warning(
                        "SLCAN RX buffer overflow (%d bytes), discarding oldest data",
                        len(self._rx_buffer),
                    )
                    self._rx_buffer = self._rx_buffer[-(RX_BUFFER_MAX_BYTES // 2):]

                self._rx_buffer = self._rx_buffer.replace("\n", "\r")
                parts = self._rx_buffer.split("\r")
                self._rx_buffer = parts[-1]
                for line in parts[:-1]:
                    line = line.strip()
                    if not line:
                        continue
                    payload = _from_slcan_line(line)
                    if payload:
                        self._on_payload(payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.warning("SLCAN read loop error on %s", self._port, exc_info=True)
                if not self._stopping:
                    await self._try_reconnect()
                    await asyncio.sleep(0.5)
