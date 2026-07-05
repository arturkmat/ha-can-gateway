#!/usr/bin/with-contenv bashio
# Deploy bundled can_gateway_v3 integration into Home Assistant /config.

INTEGRATION_SRC="/opt/integration/can_gateway_v3"
INTEGRATION_DOMAIN="can_gateway_v3"

_resolve_ha_config() {
    if [[ -d "/config" && -w "/config" ]]; then
        echo "/config"
        return 0
    fi
    if [[ -d "/homeassistant" && -w "/homeassistant" ]]; then
        echo "/homeassistant"
        return 0
    fi
    return 1
}

_integration_needs_update() {
    local dest="$1"
    python3 - <<'PY' "$dest"
import json
import sys
from pathlib import Path

dest = Path(sys.argv[1])
src_manifest = Path("/opt/integration/can_gateway_v3/manifest.json")
dst_manifest = dest / "manifest.json"

if not src_manifest.is_file():
    sys.exit(1)

def parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(value).strip().split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)

if not dst_manifest.is_file():
    sys.exit(0)

src_version = parse_version(json.loads(src_manifest.read_text(encoding="utf-8")).get("version", "0"))
dst_version = parse_version(json.loads(dst_manifest.read_text(encoding="utf-8")).get("version", "0"))
sys.exit(0 if src_version > dst_version else 1)
PY
}

_reload_custom_components() {
    local token="${SUPERVISOR_TOKEN:-}"
    if [[ -n "${token}" ]]; then
        if curl -sf -X POST \
            -H "Authorization: Bearer ${token}" \
            -H "Content-Type: application/json" \
            "http://supervisor/core/api/services/homeassistant/reload_custom_components" \
            -d '{}' >/dev/null 2>&1; then
            bashio::log.info "Requested Home Assistant custom components reload"
            return 0
        fi
    fi
    if bashio::api.supervisor POST /core/api/services/homeassistant/reload_custom_components >/dev/null 2>&1; then
        bashio::log.info "Requested Home Assistant custom components reload (supervisor API)"
        return 0
    fi
    bashio::log.warning "Could not reload Home Assistant custom components — restart Home Assistant to load integration updates"
    return 0
}

deploy_bundled_integration() {
    if [[ ! -d "${INTEGRATION_SRC}" ]]; then
        bashio::log.warning "Bundled integration missing at ${INTEGRATION_SRC}"
        return 0
    fi

    local ha_config
    if ! ha_config="$(_resolve_ha_config)"; then
        bashio::log.info "Home Assistant config volume not mapped — skip integration deploy"
        return 0
    fi

    local dest="${ha_config}/custom_components/${INTEGRATION_DOMAIN}"
    if [[ -d "${dest}" ]] && ! _integration_needs_update "${dest}"; then
        bashio::log.info "Integration ${INTEGRATION_DOMAIN} already up to date at ${dest}"
        return 0
    fi

    bashio::log.info "Deploying ${INTEGRATION_DOMAIN} to ${dest}"
    mkdir -p "${ha_config}/custom_components"
    rm -rf "${dest}"
    cp -a "${INTEGRATION_SRC}/." "${dest}/"
    bashio::log.info "Integration ${INTEGRATION_DOMAIN} deployed"
    _reload_custom_components
}

deploy_bundled_integration
