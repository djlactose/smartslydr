# config/custom_components/smartslydr/const.py

DOMAIN = "smartslydr"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Default scan interval (in seconds) for polling device data
DEFAULT_SCAN_INTERVAL = 300

# List of platform names this integration provides
PLATFORMS = [
    "cover",
    "sensor",
    "switch",
]
