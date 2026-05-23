#!/usr/bin/with-contenv bashio
# =============================================================================
# CAN Gateway add-on — SLCAN / gs_usb + web panel (Ingress) + REST API
# =============================================================================

bashio::log.info "CAN Gateway add-on starting..."

CAN_INTERFACE="$(bashio::config 'can_interface')"
CAN_PORT="$(bashio::config 'can_port')"
CAN_BITRATE="$(bashio::config 'can_bitrate')"
TTY_BAUD="$(bashio::config 'tty_baudrate')"
GSUSB_CHANNEL="$(bashio::config 'gsusb_channel')"

bashio::log.info "Interface=${CAN_INTERFACE} port=${CAN_PORT} CAN=${CAN_BITRATE} tty=${TTY_BAUD} gsusb=${GSUSB_CHANNEL}"

if [[ "${CAN_INTERFACE}" == "slcan" ]]; then
  if [[ ! -e "${CAN_PORT}" ]]; then
    bashio::log.warning "Urzadzenie ${CAN_PORT} nie istnieje — sprawdz USB i opcje dodatku"
    for dev in /dev/serial/by-id/*; do
      if [[ -e "${dev}" ]]; then
        bashio::log.info "Dostepne USB: ${dev}"
      fi
    done
  fi
elif [[ "${CAN_INTERFACE}" == "gs_usb" ]]; then
  bashio::log.info "gs_usb channel ${GSUSB_CHANNEL} — port szeregowy nie jest uzywany"
else
  bashio::log.warning "Nieznany can_interface=${CAN_INTERFACE} — uzyj slcan lub gs_usb"
fi

cd /opt || exit 1
exec python3 -m can_service
