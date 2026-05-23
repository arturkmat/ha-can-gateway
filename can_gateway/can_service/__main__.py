from __future__ import annotations

import asyncio
import logging
import signal

from .app import run_server
from .bus_manager import BusManager
from .options import load_options

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
_LOGGER = logging.getLogger(__name__)


def main() -> None:
    options = load_options()
    bus = BusManager(options)
    bus.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, loop.stop)
        except NotImplementedError:
            pass

    async def startup() -> None:
        if bus.bus_ok:
            _LOGGER.info("Running initial discovery scan (konfigurator F5)...")
            await asyncio.to_thread(bus.discovery_scan)
        else:
            _LOGGER.error("CAN bus not available — panel dziala, skan po podlaczeniu USB")

    async def run() -> None:
        await startup()
        await run_server(bus)

    try:
        loop.run_until_complete(run())
    finally:
        bus.stop()
        loop.close()


if __name__ == "__main__":
    main()
