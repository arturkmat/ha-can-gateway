from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, GATEWAY_DEVICE_ID
from .coordinator import CanGatewayCoordinator, ModuleInfo


def module_device_info(coordinator: CanGatewayCoordinator, module_id: int) -> DeviceInfo:
    info = coordinator.get_module_info(module_id)
    name = f"CAN Module {module_id} {(info.name or '').strip()}".strip()
    model = info.hw_name or (f"HW {info.hw_type}" if info.hw_type is not None else "Unknown")
    sw = info.firmware_build_datetime or info.fw_version
    return DeviceInfo(
        identifiers={(DOMAIN, f"module_{module_id}")},
        name=name,
        manufacturer="Dark-Smart",
        model=model,
        hw_version=model,
        sw_version=sw,
        serial_number=info.mac,
        via_device=(DOMAIN, GATEWAY_DEVICE_ID),
    )


def gateway_device_info() -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, GATEWAY_DEVICE_ID)},
        name="CAN Gateway v3",
        manufacturer="Dark-Smart",
        model="USB-CAN (SLCAN)",
    )
