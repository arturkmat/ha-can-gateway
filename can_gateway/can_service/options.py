from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

OPTIONS_PATH = Path("/data/options.json")

CAN_INTERFACE_SLCAN = "slcan"
CAN_INTERFACE_GS_USB = "gs_usb"


@dataclass(slots=True)
class AddonOptions:
    can_interface: str
    can_port: str
    gsusb_channel: int
    can_bitrate: int
    tty_baudrate: int
    master_key_hex: str
    auto_scan: bool
    auto_scan_interval_s: int
    mqtt_enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_topic_prefix: str
    mqtt_interval_s: int

    @property
    def master_key_bytes(self) -> bytes | None:
        key_hex = re.sub(r"\s+", "", self.master_key_hex.strip().lower())
        if len(key_hex) != 64:
            return None
        try:
            return bytes.fromhex(key_hex)
        except ValueError:
            return None


def load_options() -> AddonOptions:
    raw: dict = {}
    if OPTIONS_PATH.is_file():
        raw = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    return AddonOptions(
        can_interface=str(raw.get("can_interface", CAN_INTERFACE_SLCAN)).strip().lower(),
        can_port=str(raw.get("can_port", "/dev/ttyACM0")),
        gsusb_channel=int(raw.get("gsusb_channel", 0)),
        can_bitrate=int(raw.get("can_bitrate", 125000)),
        tty_baudrate=int(raw.get("tty_baudrate", 115200)),
        master_key_hex=str(raw.get("master_key_hex", "")),
        auto_scan=bool(raw.get("auto_scan", True)),
        auto_scan_interval_s=int(raw.get("auto_scan_interval_s", 10)),
        mqtt_enabled=bool(raw.get("mqtt_enabled", False)),
        mqtt_host=str(raw.get("mqtt_host", "core-mosquitto")),
        mqtt_port=int(raw.get("mqtt_port", 1883)),
        mqtt_username=str(raw.get("mqtt_username", "")),
        mqtt_password=str(raw.get("mqtt_password", "")),
        mqtt_topic_prefix=str(raw.get("mqtt_topic_prefix", "can_gateway")),
        mqtt_interval_s=int(raw.get("mqtt_interval_s", 5)),
    )
