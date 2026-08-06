#!/usr/bin/with-contenv bashio
# =============================================================================
# CAN Gateway add-on — SLCAN + web panel (Ingress) + REST API
# Bundled can_gateway_v3 integration deploy + Supervisor discovery on start.
# =============================================================================

bashio::log.info "CAN Gateway add-on starting..."

if [[ -x /deploy_integration.sh ]]; then
  /deploy_integration.sh
fi

CAN_INTERFACE="$(bashio::config 'connectivity.can_interface')"
CAN_PORT="$(bashio::config 'connectivity.can_port')"
CAN_BITRATE="$(bashio::config 'connectivity.can_bitrate')"
TTY_BAUD="$(bashio::config 'connectivity.tty_baudrate')"

bashio::log.info "Interface=${CAN_INTERFACE} port=${CAN_PORT} CAN=${CAN_BITRATE} tty=${TTY_BAUD}"

if [[ "${CAN_INTERFACE}" == "slcan" ]]; then
  if [[ ! -e "${CAN_PORT}" ]]; then
    bashio::log.warning "Urzadzenie ${CAN_PORT} nie istnieje — sprawdz USB i opcje dodatku"
    for dev in /dev/serial/by-id/*; do
      if [[ -e "${dev}" ]]; then
        bashio::log.info "Dostepne USB: ${dev}"
      fi
    done
  fi
else
  bashio::log.warning "Nieznany can_interface=${CAN_INTERFACE} — uzyj slcan"
fi

if [[ -x /discovery.sh ]]; then
  (
    /discovery.sh
  ) &
fi

cd /opt || exit 1
exec python3 -m can_service
