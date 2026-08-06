# CAN Gateway — Home Assistant Supervisor Add-on

Supervisor add-on for Dark-Smart CAN bus automation. It owns the USB/CAN adapter, exposes a web panel (Ingress), REST API, and **automatically installs and configures** the `can_gateway_v3` Home Assistant integration.

## Quick start (Home Assistant OS)

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add: `https://github.com/arturkmat/ha-can-gateway`
3. Install **CAN Gateway**, configure `can_port` / `can_bitrate`, start the add-on.
4. Done — no manual `custom_components` copy and no separate HACS install.

On each start the add-on:

- deploys bundled `can_gateway_v3` into `/config/custom_components/` when missing or older (manifest version compare),
- reloads custom components in Home Assistant Core,
- publishes Supervisor discovery so HA creates the integration config entry automatically (`connection_mode=addon`).

Optional: open the Ingress panel and run **Scan bus** to populate `/data/modules.json` before entities appear.

## Manual integration setup

The integration works **exclusively** in add-on mode — it talks to this add-on's REST API and has no standalone/direct-serial mode. If the add-on's auto-deploy (`deploy_integration.sh`) did not run for some reason, manually copy `custom_components/can_gateway_v3` from the same repo root into `/config/custom_components/`, restart HA, and add the integration — it still requires the add-on to be installed and running. This integration is **not** distributed via HACS — installing through both HACS and the add-on's auto-deploy at the same time caused version conflicts on the same `/config/custom_components/can_gateway_v3/` folder.

## API (port 8099)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/discovery` | Saved modules + scan metadata |
| GET | `/api/modules` | Module list |
| POST | `/api/scan` | Bus scan + persist to `/data` |
| GET | `/api/state` | Runtime snapshot for integration |
| POST | `/api/can/send` | Generic CAN TX |

## Repository layout

```
can_gateway/
  integration/can_gateway_v3/   # bundled HA integration (sync from repo custom_components/)
  deploy_integration.sh         # copy + reload on start
  discovery.sh                  # Supervisor discovery for auto config entry
  can_service/                  # REST API + CAN bridge
```

Source of truth for integration code: `custom_components/can_gateway_v3/` in [ha-can-gateway](https://github.com/arturkmat/ha-can-gateway) (sync with `tools/sync_addon_integration.ps1`).
