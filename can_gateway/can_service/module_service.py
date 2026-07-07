"""Operacje na module CAN — nazwa, ID, identify."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from protocol_constants import (
    COMMAND_GET_MODULE_NAME,
    COMMAND_IDENTIFY,
    COMMAND_SET_MODULE_ID_BY_MAC,
    COMMAND_SET_MODULE_NAME,
    MODULE_NAME_CHUNK_READ,
    MODULE_NAME_MAX_LEN,
)

if TYPE_CHECKING:
    from .bus_manager import BusManager


def _ascii_payload(name: str) -> list[int]:
    data = [ord(c) & 0xFF for c in str(name)[:32]]
    while len(data) < 6:
        data.append(0)
    return data[:6]


def read_module_name_chunked(bus: BusManager, module_id: int, *, timeout: float = 0.6) -> str | None:
    parts: list[str] = []
    expected_total: int | None = None
    for offset in range(0, MODULE_NAME_MAX_LEN, MODULE_NAME_CHUNK_READ):
        resp = bus.send_config_and_wait(
            int(module_id),
            COMMAND_GET_MODULE_NAME,
            [offset],
            timeout=timeout,
        )
        if resp is None or len(resp) < 8 or int(resp[2]) != 0:
            return None
        total_len = int(resp[3])
        resp_offset = int(resp[4])
        if resp_offset != offset:
            return None
        if expected_total is None:
            expected_total = total_len
        elif expected_total != total_len:
            return None
        if total_len == 0:
            return ""
        chars = [value for value in resp[5:8] if value != 0]
        parts.append(bytes(chars).decode("ascii", errors="ignore"))
        if offset + MODULE_NAME_CHUNK_READ >= total_len:
            break
    limit = expected_total if expected_total is not None else MODULE_NAME_MAX_LEN
    return "".join(parts)[:limit].strip()


def get_module_name(bus: BusManager, module_id: int) -> dict[str, Any]:
    name = read_module_name_chunked(bus, module_id)
    if name is None:
        return {"ok": False, "error": "read failed"}
    return {"ok": True, "module_id": int(module_id), "name": name}


def set_module_name(bus: BusManager, module_id: int, name: str) -> dict[str, Any]:
    resp = bus.send_config_and_wait(
        module_id,
        COMMAND_SET_MODULE_NAME,
        _ascii_payload(name),
        timeout=0.8,
    )
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return {"ok": False, "error": "set failed"}
    engine = bus._get_engine()  # noqa: SLF001
    ctx = engine.context(int(module_id))
    ctx.name = str(name)
    for item in engine.discovered_modules:
        if item.get("module_id") == int(module_id):
            item["name"] = str(name)
            break
    return {"ok": True, "module_id": int(module_id), "name": str(name)}


def identify_module(bus: BusManager, module_id: int, seconds: int = 5) -> dict[str, Any]:
    sec = max(1, min(30, int(seconds)))
    resp = bus.send_config_and_wait(module_id, COMMAND_IDENTIFY, [sec], timeout=0.5)
    ok = resp is not None and len(resp) >= 3 and int(resp[2]) == 0
    return {"ok": ok, "module_id": int(module_id), "seconds": sec}


def set_module_id_by_mac(
    bus: BusManager,
    mac_hex: str,
    new_module_id: int,
) -> dict[str, Any]:
    cleaned = str(mac_hex).replace(":", "").replace("-", "").strip()
    if len(cleaned) != 12:
        return {"ok": False, "error": "MAC must be 12 hex chars"}
    try:
        mac_bytes = [int(cleaned[i : i + 2], 16) for i in range(0, 12, 2)]
    except ValueError:
        return {"ok": False, "error": "invalid MAC hex"}
    mid = int(new_module_id)
    if not (1 <= mid <= 254):
        return {"ok": False, "error": "module_id must be 1..254"}
    args = mac_bytes + [mid, 0]
    resp = bus.send_config_and_wait(0xFF, COMMAND_SET_MODULE_ID_BY_MAC, args[:8], timeout=2.0)
    if resp is None or len(resp) < 3 or int(resp[2]) != 0:
        return {"ok": False, "error": "SET_MODULE_ID_BY_MAC failed"}
    return {"ok": True, "mac": cleaned.upper(), "module_id": mid}
