ARG BUILD_FROM
FROM $BUILD_FROM

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/lib:/opt/can_service

RUN pip3 install --no-cache-dir "python-can>=4.4,<5" "cryptography>=45" "aiohttp>=3.9" "paho-mqtt>=2"

COPY run.sh /
RUN chmod a+x /run.sh

COPY lib /lib
COPY can_service /opt/can_service
COPY static /opt/can_service/static

RUN PYTHONPATH=/lib python3 -c "from protocol_constants import CAN_ID_RELAY_GPIO_MAP, MCP23017_RELAY_CAN_BASE; print('protocol ok', CAN_ID_RELAY_GPIO_MAP)"

WORKDIR /opt

CMD [ "/run.sh" ]
