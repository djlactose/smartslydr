# config/custom_components/smartslydr/const.py

DOMAIN = "smartslydr"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"

# Per-device option keys for the move-duration calibration system in
# cover.py. The user-supplied override (if set) wins over the auto-
# calibrated value, which wins over DEFAULT_MOVE_DURATION.
MOVE_DURATION_OPTION_PREFIX = "move_duration_"
CALIBRATED_DURATION_OPTION_PREFIX = "calibrated_move_duration_"
DEFAULT_MOVE_DURATION = 10.0

SERVICE_RECALIBRATE_COVER = "recalibrate_cover"

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
