# Threat model — DARK-SMART: RustDesk overlay + CAN Smart Home

Scope: `arturkmat/rustdesk` (DARK-SMART overlay only), `arturkmat/can-control-suite`, `arturkmat/ha-can-gateway`. No infrastructure repository exists (user confirmed); hbbs/hbbr server deployment is not covered.

## System overview and trust boundaries

**CAN Smart Home** — home automation on a wired CAN bus (V3 transport `can_id = (module_id << 3) | frame_type`, 125 kbit/s, 11-bit). Production traffic is **plaintext with no authentication or replay protection** (`docs/README.md`; Secure TLV / MASTER_KEY in `CAN-HUB-STM32/CAN_SECURITY.md` and `docs/archive/` is historical, not deployed). Any node on the bus can send CONFIG (`0x7F8` broadcast), relay/shutter commands, bind events and OTA frames.

Trust boundaries:
1. CAN bus ↔ modules (no auth on the bus).
2. BLE radio ↔ module (BLE OTA GATT `0xFE70`, optional 4+ digit PIN, 5 tries / 60 s lockout).
3. HA network / HA users ↔ HA add-on REST API (aiohttp, ingress port 8099) ↔ SLCAN serial ↔ bus.
4. Files from outside (firmware `.bin`, profile/JSON imports) ↔ Windows configurator ↔ bus.
5. GitHub (fork releases, Pages, CI secrets) ↔ RustDesk clients installed on customer machines.

## Components

| Component | Code |
|---|---|
| **Module firmware** | `can-control-suite/firmware_uniwersalne_can/` (ESP-IDF, `idf_build/main/main.cpp` CONFIG handlers, OTA bridge; `ble_ota.cpp`) |
| **CAN hub** | `can-control-suite/CAN-HUB-STM32/firmware/` (STM32G474 FDCAN forwarder, SLCAN, SPI link, `filter.c`) |
| **Configurator** | `can-control-suite/konfigurator_windows_usb_can/`, `protocol/`, `tools/` (Python/Tkinter, SLCAN, CAN/BLE/COM flashing, profile import/export) |
| **HA gateway** | `ha-can-gateway/can_gateway/` (add-on: REST API, web panel, SLCAN bus manager, OTA upload) and `ha-can-gateway/custom_components/can_gateway_v3/` (HA integration) |
| **RustDesk overlay** | `rustdesk/src/branding.rs`, `src/updater.rs`, `src/updater/artifact.rs`, overlay parts of `src/common.rs`, `build.rs`, `custom/` |
| **Distribution & CI** | `rustdesk/pages/` (download site), `rustdesk/.github/workflows/` (release-win-android, pages, flutter-build; server address/key from secrets) |

## Entry points

- CAN frames from any bus node: CONFIG commands (factory reset, set module id by MAC, service mode, GPIO roles, mappings, BLE OTA enable 103 / PIN 104-105, OTA 60-64 + OTA_DATA).
- BLE GATT `0xFE70`-`0xFE74` while the BLE window is open (CONFIG 103 or 10 s button hold).
- HA add-on REST: `/api/can/send` (raw frame), relays, shutters, OTA upload, factory reset, GPIO, mappings, set-id-by-mac (`can_gateway/can_service/app.py`).
- Configurator: firmware files, profile/backup JSON, `configurator_settings.json`, SLCAN responses from the bus.
- RustDesk: update check against `api.github.com/repos/arturkmat/rustdesk/releases/tags/dark-smart`, asset download verified by GitHub asset `sha256` digest; compiled-in rendezvous/relay/API/key settings (`branding.rs`); Pages download page JS.

## Assets (user priorities)

- **Critical (user):** home control — relays, shutters, module OTA/firmware integrity and availability.
- Also relevant: RustDesk clients on customer machines (remote access), the fork update channel, CI secrets (server address/public key, signing).

## Attackers (user: all in scope)

- **On the CAN bus** — anyone who can attach to or compromise a node on the bus (user: in scope, not accepted risk).
- **In BLE range** — neighbour / passer-by (user: in scope).
- **Malicious file** — firmware image or profile/config file handed to the configurator (user: in scope).
- HA network / HA user reaching the add-on API (inferred).
- Supply chain: whoever controls the GitHub account/releases/CI (inferred).

## Existing controls

- BLE OTA: no BLE unless enabled by CONFIG 103 / button hold; time-limited window; optional PIN with lockout.
- CAN OTA: host checks `HW_TYPE` before upload; ESP-IDF image validation at `esp_ota_end`; no secure boot / signature check found.
- Service-mode timeout for sensitive config.
- HA add-on behind HA ingress (HA auth) — direct port exposure to be confirmed.
- RustDesk updater: HTTPS GitHub only, pinned owner/repo/tag, sha256 digest from GitHub API, filename allow-list.

## Out of scope / accepted risk

- Upstream RustDesk code not touched by the DARK-SMART overlay (user: scan the overlay only).
- (User did **not** accept CAN bus access, BLE range or malicious files as risk.)

## Open questions

- Is the HA add-on port 8099 ever exposed outside ingress?
- Are modules flashed with secure boot / flash encryption in production?
- Who besides you has push access to `arturkmat/rustdesk` releases?
