#!/usr/bin/with-contenv bashio
# Publish Supervisor discovery for can_gateway_v3 integration auto-setup.

declare discovery_config

bashio::net.wait_for 8099 127.0.0.1 120

discovery_config="$(
    bashio::var.json \
        host "$(hostname)" \
        port "^8099"
)"

if bashio::discovery "can_gateway_v3" "${discovery_config}" > /dev/null; then
    bashio::log.info "Sent can_gateway_v3 discovery to Home Assistant"
else
    bashio::log.error "Failed to send can_gateway_v3 discovery to Home Assistant"
fi
