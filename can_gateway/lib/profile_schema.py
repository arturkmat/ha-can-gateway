"""JSON profile schema validation for configurator save/load."""

from __future__ import annotations

from typing import Any

PROFILE_SCHEMA_VERSION = 1


def validate_profile_dict(profile: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]

    version = profile.get("schema_version", PROFILE_SCHEMA_VERSION)
    if not isinstance(version, int) or version < 1 or version > PROFILE_SCHEMA_VERSION:
        errors.append(
            f"unsupported schema_version={version!r} (expected 1..{PROFILE_SCHEMA_VERSION})"
        )

    usb_can = profile.get("usb_can")
    if usb_can is not None:
        if not isinstance(usb_can, dict):
            errors.append("usb_can must be an object")
        else:
            iface = str(usb_can.get("interface", "slcan")).strip().lower()
            if iface not in {"slcan", "gs_usb"}:
                errors.append(f"usb_can.interface must be slcan or gs_usb, got {iface!r}")

    for key in ("module_aliases", "service", "mapping"):
        value = profile.get(key)
        if value is not None and not isinstance(value, dict):
            errors.append(f"{key} must be an object")

    return errors
