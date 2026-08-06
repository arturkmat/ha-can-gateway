from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

OPTIONS_PATH = Path("/data/options.json")

CAN_INTERFACE_SLCAN = "slcan"


def _parse_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off", ""):
            return False
    if value is None:
        return default
    return bool(value)


@dataclass(slots=True)
class AddonOptions:
    can_interface: str
    can_port: str
    can_bitrate: int
    tty_baudrate: int
    auto_scan: bool
    auto_scan_interval_s: int
    mqtt_enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_topic_prefix: str
    mqtt_interval_s: int


def _sub(raw: dict, key: str) -> dict:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def load_options() -> AddonOptions:
    raw: dict = {}
    if OPTIONS_PATH.is_file():
        raw = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    connectivity = _sub(raw, "connectivity")
    auto_scan_cfg = _sub(raw, "auto_scan")
    mqtt = _sub(raw, "mqtt")
    return AddonOptions(
        can_interface=str(connectivity.get("can_interface", CAN_INTERFACE_SLCAN)).strip().lower(),
        can_port=str(connectivity.get("can_port", "/dev/ttyACM0")),
        can_bitrate=int(connectivity.get("can_bitrate", 125000)),
        tty_baudrate=int(connectivity.get("tty_baudrate", 115200)),
        auto_scan=_parse_bool(auto_scan_cfg.get("enabled"), True),
        auto_scan_interval_s=int(auto_scan_cfg.get("interval_s", 10)),
        mqtt_enabled=_parse_bool(mqtt.get("enabled"), False),
        mqtt_host=str(mqtt.get("host", "core-mosquitto")),
        mqtt_port=int(mqtt.get("port", 1883)),
        mqtt_username=str(mqtt.get("username", "")),
        mqtt_password=str(mqtt.get("password", "")),
        mqtt_topic_prefix=str(mqtt.get("topic_prefix", "can_gateway")),
        mqtt_interval_s=int(mqtt.get("interval_s", 5)),
    )
