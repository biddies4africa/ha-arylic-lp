"""Describe auto-power events for the logbook (Activity)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_AUTOPOWER

REASONS = {
    "playback_started": "playback started",
    "idle": "idle",
    "player_unreachable": "player unreachable",
}


def _message(data: dict[str, Any]) -> str:
    on = data.get("action") == "turn_on"
    switch = data.get("power_switch_name") or data.get("power_switch")
    attempt = data.get("attempt")
    error = data.get("error")
    match data.get("result"):
        case "requested":
            reason = REASONS.get(data.get("reason", ""), data.get("reason"))
            return f"turning {'on' if on else 'off'} {switch} ({reason})"
        case "attempt_failed":
            return f"attempt {attempt} failed: {error}"
        case "succeeded":
            return f"turned {'on' if on else 'off'} {switch} (attempt {attempt})"
        case "gave_up":
            return f"gave up after {attempt} attempts"
        case "cancelled":
            return f"stopped retrying: {error}"
    return f"{data.get('action')} {switch}: {data.get('result')}"


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe arylic_lp logbook events."""

    @callback
    def describe(event: Event) -> dict[str, str]:
        data = event.data
        entity_id = data.get("entity_id")
        state = hass.states.get(entity_id) if entity_id else None
        description = {
            LOGBOOK_ENTRY_NAME: state.name if state else "Auto-power",
            LOGBOOK_ENTRY_MESSAGE: _message(data),
        }
        if entity_id:
            description[LOGBOOK_ENTRY_ENTITY_ID] = entity_id
        return description

    async_describe_event(DOMAIN, EVENT_AUTOPOWER, describe)
