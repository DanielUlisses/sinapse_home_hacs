"""Constants for the Sinapse Home integration."""
from datetime import timedelta

DOMAIN = "sinapse_home"
MANUFACTURER = "Sinapse"

# Private ESP RainMaker deployment used by the Sinapse Home app (com.sinapse.sinapsehome).
BASE_URL = "https://90h350n1tc.execute-api.sa-east-1.amazonaws.com/dev/v1"

SCAN_INTERVAL = timedelta(seconds=30)
REFRESH_AFTER_WRITE = 3  # seconds

PLATFORMS = ["binary_sensor", "button", "climate", "light", "sensor", "switch"]

# ESP RainMaker standard types
DEV_SWITCH = "esp.device.switch"
DEV_LIGHT = "esp.device.light"
DEV_THERMOSTAT = "esp.device.thermostat"

P_NAME = "esp.param.name"
P_POWER = "esp.param.power"
P_BRIGHTNESS = "esp.param.brightness"
P_HUE = "esp.param.hue"
P_SATURATION = "esp.param.saturation"
P_SETPOINT = "esp.param.setpoint-temperature"
P_TEMPERATURE = "esp.param.temperature"
P_AC_MODE = "esp.param.ac-mode"
P_TOGGLE = "esp.ui.toggle"

# Sinapse custom params
P_COOLING = "parametro_refrigerando"

# Params consumed by a dedicated entity (light/climate/switch) instead of the generic ones.
HANDLED_BY_DEVICE = {
    DEV_SWITCH: {P_POWER},
    DEV_LIGHT: {P_POWER, P_BRIGHTNESS, P_HUE, P_SATURATION},
    DEV_THERMOSTAT: {P_SETPOINT, P_AC_MODE, P_POWER},
}

# Local control (ESP local ctrl over the LAN)
LOCAL_CTRL_SERVICE = "_esp_local_ctrl._tcp.local."
LOCAL_TIMEOUT = 5  # seconds, per HTTP request
LOCAL_CONNECT_TIMEOUT = 3  # seconds; a connect timeout means nothing was sent
LOCAL_SCAN_INTERVAL = timedelta(seconds=5)
LOCAL_MAX_FAILURES = 3  # consecutive failed local polls before falling back to the cloud
LOCAL_DEFAULT_PORT = 8080  # ESP-IDF default, only for the manual host option
CLOUD_REFRESH_INTERVAL = timedelta(minutes=30)  # node config + POP while local works
DATA_LOCAL_ADDRESSES = f"{DOMAIN}_local_addresses"  # hass.data: {node_id: (host, port)}
CONF_NODE_CACHE = "node_cache"  # entry.data: last good node config, Local Control values, LAN address

SERVICE_LOCAL_CONTROL = "esp.service.local_control"
P_LOCAL_CONTROL_POP = "esp.param.local_control_pop"
P_LOCAL_CONTROL_TYPE = "esp.param.local_control_type"

CONNECTION_LOCAL = "local"
CONNECTION_CLOUD = "cloud"
