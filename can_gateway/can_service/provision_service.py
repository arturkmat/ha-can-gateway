"""MASTER_KEY provisioning — jak panel serwisowy konfiguratora Windows."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from protocol_constants import (
    COMMAND_PROVISION_APPLY_MASTER_KEY,
    COMMAND_PROVISION_GET_MASTER_KEY_STATE,
    COMMAND_PROVISION_GET_STATE,
    COMMAND_PROVISION_SET_MASTER_KEY_PART,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager


def _parse_master_key_hex(master_key_hex: str) -> bytes:
    key = bytes.fromhex(str(master_key_hex).strip())
    if len(key) != 32:
        raise ValueError("MASTER_KEY must be 64 hex chars (32 bytes)")
    return key


def get_master_key_state(bus: BusManager, module_id: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_PROVISION_GET_MASTER_KEY_STATE, timeout=1.0)
    if resp is None or len(resp) < 4:
        return {"ok": False, "error": "no response"}
    if int(resp[2]) != 0:
        return {"ok": False, "error": f"status={int(resp[2])}"}
    return {
        "ok": True,
        "module_id": int(module_id),
        "has_master_key": bool(int(resp[3])),
        "parts_mask": int(resp[4]) if len(resp) > 4 else None,
    }


def get_provision_state(bus: BusManager, module_id: int) -> dict[str, Any]:
    resp = bus.send_config_and_wait(module_id, COMMAND_PROVISION_GET_STATE, timeout=1.0)
    if resp is None or len(resp) < 4:
        return {"ok": False, "error": "no response"}
    if int(resp[2]) != 0:
        return {"ok": False, "error": f"status={int(resp[2])}"}
    return {
        "ok": True,
        "module_id": int(module_id),
        "has_target_mac": bool(int(resp[3])) if len(resp) > 3 else False,
        "parts_mask": int(resp[4]) if len(resp) > 4 else 0,
        "has_key": bool(int(resp[5])) if len(resp) > 5 else False,
    }


def _send_master_key_parts(bus: BusManager, module_id: int, master_key: bytes) -> dict[str, Any]:
    for part_idx in range(8):
        chunk = master_key[part_idx * 4 : (part_idx + 1) * 4]
        resp = bus.send_config_and_wait(
            module_id,
            COMMAND_PROVISION_SET_MASTER_KEY_PART,
            [part_idx, chunk[0], chunk[1], chunk[2], chunk[3]],
            timeout=2.0,
        )
        if resp is None or len(resp) < 3 or int(resp[2]) != 0:
            return {"ok": False, "error": f"SET_MASTER_KEY_PART part={part_idx} failed"}
        time.sleep(0.02)
    resp = bus.send_config_and_wait(module_id, COMMAND_PROVISION_APPLY_MASTER_KEY, timeout=3.0)
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return {"ok": False, "error": "APPLY_MASTER_KEY failed"}
    engine = bus._get_engine()  # noqa: SLF001
    for item in engine.discovered_modules:
        if item.get("module_id") == int(module_id):
            item["has_master_key"] = True
            break
    engine._module_key_mismatch.discard(int(module_id))  # noqa: SLF001
    return {"ok": True, "module_id": int(module_id)}


def send_master_key_to_module(
    bus: BusManager,
    module_id: int,
    master_key_hex: str,
) -> dict[str, Any]:
    try:
        master_key = _parse_master_key_hex(master_key_hex)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    state = get_master_key_state(bus, module_id)
    if not state.get("ok"):
        return state
    return _send_master_key_parts(bus, module_id, master_key)
