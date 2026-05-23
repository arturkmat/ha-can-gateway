"""Optional MQTT publisher — stan magistrali dla automatyzacji HA / Node-RED."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bus_manager import BusManager
    from .options import AddonOptions

_LOGGER = logging.getLogger(__name__)


class MqttBridge:
    def __init__(self, bus: BusManager, options: AddonOptions) -> None:
        self._bus = bus
        self._options = options
        self._client = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self._options.mqtt_enabled:
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            _LOGGER.warning("paho-mqtt not installed — MQTT disabled")
            return

        prefix = self._options.mqtt_topic_prefix.rstrip("/")
        client = mqtt.Client(client_id="can_gateway_addon")
        if self._options.mqtt_username:
            client.username_pw_set(self._options.mqtt_username, self._options.mqtt_password)
        try:
            client.connect(
                self._options.mqtt_host,
                int(self._options.mqtt_port),
                keepalive=30,
            )
            client.loop_start()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("MQTT connect failed: %s", err)
            return

        self._client = client
        _LOGGER.info(
            "MQTT connected %s:%d topic=%s/state",
            self._options.mqtt_host,
            self._options.mqtt_port,
            prefix,
        )
        self._task = asyncio.create_task(self._publish_loop(prefix))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("MQTT disconnect error", exc_info=True)
            self._client = None

    async def _publish_loop(self, prefix: str) -> None:
        last_payload: str | None = None
        while True:
            try:
                snapshot = self._bus.full_state()
                payload = json.dumps(snapshot, separators=(",", ":"), default=_json_default)
                if payload != last_payload and self._client is not None:
                    self._client.publish(f"{prefix}/state", payload, qos=0, retain=True)
                    self._client.publish(
                        f"{prefix}/status",
                        json.dumps(snapshot.get("status", {})),
                        qos=0,
                        retain=True,
                    )
                    last_payload = payload
            except Exception:  # noqa: BLE001
                _LOGGER.debug("MQTT publish failed", exc_info=True)
            await asyncio.sleep(max(2, int(self._options.mqtt_interval_s)))


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")
