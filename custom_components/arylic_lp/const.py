"""Constants for the Arylic - LP Series integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "arylic_lp"

CONF_POWER_SWITCH: Final = "power_switch"
CONF_AIRPLAY_FALLBACK: Final = "airplay_fallback"

DEFAULT_NAME: Final = "Arylic LP"
DEFAULT_OFF_DELAY_MIN: Final = 30
MIN_OFF_DELAY_MIN: Final = 1
MAX_OFF_DELAY_MIN: Final = 240

CONTROL_PORT: Final = 2018
AIRPLAY_PORT: Final = 7000
DISCOVERY_PORT: Final = 1800

# Play state is polled over one persistent connection; status (volume/source)
# changes less often and is refreshed on a slower cadence.
SCAN_INTERVAL: Final = timedelta(seconds=2)
STATUS_REFRESH_SECONDS: Final = 30

# Consecutive failed polls tolerated (holding the last state) before entities
# go unavailable, so a single network blip doesn't flap the sensor.
FAILURE_GRACE_POLLS: Final = 3
# How long the player must be unreachable before a Repairs issue is raised.
UNREACHABLE_ISSUE_AFTER: Final = timedelta(minutes=10)
# Minimum auto-off countdown while the player is unreachable. The LP10 restarts
# its network stack now and then (~90 s offline), which says nothing about
# whether it is still playing.
UNREACHABLE_OFF_DELAY: Final = timedelta(minutes=60)

# Power switch actions: attempts, and how long to wait for the switch state to
# confirm before retrying (cloud-backed plugs can lag).
POWER_ATTEMPTS: Final = 3
POWER_CONFIRM_SECONDS: Final = 10
