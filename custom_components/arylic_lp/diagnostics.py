"""Diagnostics for Arylic - LP Series."""

from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import LPConfigEntry

# Identity fields (from the player's UDP 1800 reply) that are safe to share:
# what the model and firmware are, which helps with other models. Diagnostics
# get pasted into public issues, so anything else (the LP10 also sends its
# address, MAC as "usn" and time zone) is left out with only its name listed.
SHARED_IDENTITY_FIELDS = (
    "devicename",
    "cast_model",
    "fwversion",
    "cast_fwversion",
    "version",
    "speakertype",
    "source_list",
    "mramode",
    "netmode",
    "wifiband",
    "state",
    "fn",
    "port",
    "tcpport",
)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _shared_identity(identity: dict[str, Any]) -> dict[str, Any]:
    shared: dict[str, Any] = {
        key: identity[key] for key in SHARED_IDENTITY_FIELDS if key in identity
    }
    shared["withheld_fields"] = sorted(set(identity) - set(SHARED_IDENTITY_FIELDS))
    return shared


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LPConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    coordinator = data.coordinator
    state = coordinator.data
    host = entry.data[CONF_HOST]

    def scrub(text: str | None) -> str | None:
        """Errors can name the player's (or a local plug's) address."""
        return _IPV4.sub(REDACTED, text.replace(host, REDACTED)) if text else text

    autopower = data.autopower.diagnostics() if data.autopower else None
    if autopower is not None:
        autopower["last_error"] = scrub(autopower["last_error"])

    return {
        "entry": {
            "data": async_redact_data(
                {**entry.data, "identity": _shared_identity(entry.data.get("identity", {}))},
                {CONF_HOST},
            ),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "consecutive_failures": coordinator.consecutive_failures,
            "drops": coordinator.drops,
            "last_drop": coordinator.last_drop.isoformat() if coordinator.last_drop else None,
            "last_error": scrub(coordinator.last_error),
            "state": asdict(state) if state is not None else None,
        },
        "client": {"connected": data.client.connected, **data.client.stats},
        "autopower": autopower,
    }
