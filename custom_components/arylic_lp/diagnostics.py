"""Diagnostics for Arylic - LP Series."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LPConfigEntry

TO_REDACT = {"usn", "host"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LPConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    coordinator = data.coordinator
    state = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(
                {
                    **entry.data,
                    "identity": async_redact_data(entry.data.get("identity", {}), TO_REDACT),
                },
                TO_REDACT,
            ),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "consecutive_failures": coordinator.consecutive_failures,
            "drops": coordinator.drops,
            "last_drop": coordinator.last_drop.isoformat() if coordinator.last_drop else None,
            "last_error": coordinator.last_error,
            "state": asdict(state) if state is not None else None,
        },
        "client": {"connected": data.client.connected, **data.client.stats},
        "autopower": data.autopower.diagnostics() if data.autopower else None,
    }
