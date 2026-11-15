#!/command/with-contenv bashio

if ! bashio::config.has_value 'mesh_key'; then
    bashio::exit.nok "Set the light's eight-hex-digit mesh_key in the Configuration tab"
fi

if ! bashio::services.available 'mqtt'; then
    bashio::exit.nok "No MQTT service is available. Install and start the Mosquitto broker app."
fi

export BRMESH_KEY="$(bashio::config 'mesh_key')"
export BRMESH_ADDRESS="$(bashio::config 'address')"
export BRMESH_DEVICE_NAME="$(bashio::config 'device_name')"
export BRMESH_ADAPTER_INDEX="$(bashio::config 'adapter_index')"
export BRMESH_RADIO_BACKEND="$(bashio::config 'radio_backend')"
export BRMESH_AUTO_PAIR="$(bashio::config 'auto_pair')"
export BRMESH_ADVERTISE_SECONDS="$(bashio::config 'advertise_seconds')"
export BRMESH_MQTT_PREFIX="$(bashio::config 'mqtt_topic_prefix')"

export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
export MQTT_SSL="$(bashio::services 'mqtt' 'ssl')"

bashio::log.info "Starting BRMesh controller for address ${BRMESH_ADDRESS} using ${BRMESH_RADIO_BACKEND} on hci${BRMESH_ADAPTER_INDEX}"
exec /opt/brmesh-venv/bin/python3 /app/app.py
