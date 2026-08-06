ARG BUILD_FROM
FROM $BUILD_FROM

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/lib:/opt/can_service

RUN pip3 install --no-cache-dir "python-can>=4.4,<5" "pyserial>=3.5" "cryptography>=45" "aiohttp>=3.9" "paho-mqtt>=2"

COPY run.sh deploy_integration.sh discovery.sh /
RUN chmod a+x /run.sh /deploy_integration.sh /discovery.sh

# lib/ = te same pliki co konfigurator_windows_usb_can (protocol_constants, configurator_engine, …)
COPY lib /lib
COPY can_service /opt/can_service
COPY static /opt/can_service/static
COPY integration/can_gateway_v3 /opt/integration/can_gateway_v3

RUN PYTHONPATH=/lib python3 -c "import serial; from configurator_engine import ConfiguratorEngine; from protocol_constants import can_v2_config_request_id; print('deps ok', serial.__version__, hex(can_v2_config_request_id(1)))"

RUN test -f /opt/can_service/static/index.html && test -f /opt/can_service/static/app.js

WORKDIR /opt

CMD [ "/run.sh" ]
