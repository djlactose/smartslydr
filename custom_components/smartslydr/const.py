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

# Floor for the user-configurable scan interval.
#
# Every poll costs two upstream requests (/devices + /operation/get), and
# /operation/get fans out one command entry per device. The AWS API
# Gateway in front of the SmartSlydr backend enforces an undocumented
# per-account throttle: a sustained ~10s cadence returns HTTP 429 on
# essentially every /operation/get call, and because writes (/operation)
# share that throttle, the user's own open/close commands get rejected
# alongside the polling. That is not a recoverable state - it persists
# for as long as the polling continues.
#
# The options flow used to accept a 10s minimum, which let a user
# configure exactly that failure. 30s is the value the README has always
# recommended and leaves headroom for the 10s fast-poll burst after a
# command (see FAST_POLL_INTERVAL_S in __init__.py), which is bounded to
# 30 seconds and so can't drain the quota on its own.
MIN_SCAN_INTERVAL = 30

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
