# config/custom_components/smartslydr/const.py

DOMAIN = "smartslydr"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"

# Default scan interval (in seconds) for polling device data
DEFAULT_SCAN_INTERVAL = 300

# Default upstream API base. Overridable per-entry via the options flow
# so a future LycheeThings domain rotation, or a local proxy for
# debugging, doesn't require a code change.
DEFAULT_BASE_URL = (
    "https://34yl6ald82.execute-api.us-east-2.amazonaws.com/prod"
)

# List of platform names this integration provides
PLATFORMS = [
    "cover",
    "sensor",
    "switch",
]
