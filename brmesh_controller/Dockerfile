ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}

RUN apk add --no-cache bluez bluez-btmgmt py3-dbus py3-gobject3 python3 py3-pip \
    && python3 -m venv --system-site-packages /opt/brmesh-venv \
    && /opt/brmesh-venv/bin/pip install --no-cache-dir paho-mqtt==2.1.0

WORKDIR /app
COPY app.py brmesh_protocol.py /app/
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: BRMesh Floodlight Controller" \
    org.opencontainers.image.description="Local BLE advertisement controller for BLFL-LFCA/FastCon lights" \
    org.opencontainers.image.source="https://github.com/Kyguyog/BLFL-LFCA-Floodlight-Reverse-Engineer"
