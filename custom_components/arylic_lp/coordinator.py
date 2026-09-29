"""Polling coordinator for one player."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import (
    LPClient,
    LPError,
    LPProtocolError,
    LPStatus,
    async_airplay_active,
    parse_play_state,
    parse_setting,
    parse_status,
)
from .const import (
    DOMAIN,
    FAILURE_GRACE_POLLS,
    SCAN_INTERVAL,
    STATUS_REFRESH_SECONDS,
    UNREACHABLE_ISSUE_AFTER,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LPState:
    """Snapshot shared by all entities of one player."""

    playing: bool
    pla: bool | None
    airplay: bool | None
    status: LPStatus | None


class LPCoordinator(DataUpdateCoordinator[LPState]):
    """Poll play state every couple of seconds over one persistent connection.

    Frames the player sends on its own (it broadcasts every reply to all
    connected clients, e.g. after another app changes a setting) are applied
    as they arrive, so changes show up without waiting for the next poll.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: LPClient,
        *,
        airplay_fallback: bool,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"Arylic LP {client.host}",
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.airplay_fallback = airplay_fallback
        self.consecutive_failures = 0
        self.unreachable_since: float | None = None
        self.last_error: str | None = None
        # Times the player went unavailable; the drops sensor restores these.
        self.drops = 0
        self.last_drop: datetime | None = None
        self._status: LPStatus | None = None
        self._status_at = 0.0
        client.add_frame_listener(self._handle_frame)

    @property
    def issue_id(self) -> str:
        return f"unreachable_{self.config_entry.entry_id}"

    def request_status_refresh(self) -> None:
        """Re-read volume/source on the next poll (after a control write)."""
        self._status_at = 0.0

    async def _async_update_data(self) -> LPState:
        pla: bool | None = None
        airplay: bool | None = None
        error: str | None = None
        try:
            pla = await self.client.get_playing()
        except LPError as err:
            error = str(err)

        # AirPlay fallback: only consulted when PLA doesn't already say playing.
        if not pla and self.airplay_fallback:
            try:
                airplay = await async_airplay_active(self.client.host)
            except LPError as err:
                error = error or str(err)

        # "No AirPlay session" says nothing about other sources, so without a
        # PLA answer only a positive AirPlay reading counts as a good poll.
        if pla is None and not airplay:
            return self._handle_failure(error or "No answer")

        if pla is not None and time.monotonic() - self._status_at >= STATUS_REFRESH_SECONDS:
            await self._async_refresh_status()

        self._handle_success()
        return LPState(
            playing=bool(pla) or bool(airplay),
            pla=pla,
            airplay=airplay,
            status=self._status,
        )

    async def _async_refresh_status(self) -> None:
        """Read volume/source/tone. Mid and virtual bass aren't in ``STA``."""
        try:
            status = await self.client.get_status()
        except LPError as err:
            _LOGGER.debug("Status refresh for %s failed: %s", self.client.host, err)
            return
        previous = self._status
        mid = previous.mid if previous else None
        virtual_bass = previous.virtual_bass if previous else None
        try:
            mid = await self.client.get_mid()
            virtual_bass = await self.client.get_virtual_bass()
        except LPError as err:
            _LOGGER.debug("Tone refresh for %s failed: %s", self.client.host, err)
        self._status = replace(status, mid=mid, virtual_bass=virtual_bass)
        self._status_at = time.monotonic()

    @callback
    def _handle_frame(self, frame: str) -> None:
        """Apply a frame the player sent without us asking for it."""
        data = self.data
        if data is None or not self.last_update_success:
            return
        if frame.startswith("PLA:"):
            try:
                pla = parse_play_state(frame)
            except LPProtocolError:
                return
            new = replace(data, pla=pla, playing=pla or bool(data.airplay))
        elif frame.startswith("STA:"):
            try:
                status = parse_status(frame)
            except LPProtocolError:
                return
            if data.status is not None:
                status = replace(status, mid=data.status.mid, virtual_bass=data.status.virtual_bass)
            self._status = status
            new = replace(data, status=status)
        elif (setting := parse_setting(frame)) is not None and data.status is not None:
            field, value = setting
            self._status = replace(data.status, **{field: value})
            new = replace(data, status=self._status)
        else:
            return
        if new != data:
            self.async_set_updated_data(new)

    def _handle_failure(self, error: str) -> LPState:
        self.consecutive_failures += 1
        self.last_error = error
        now = time.monotonic()
        if self.unreachable_since is None:
            self.unreachable_since = now
        if now - self.unreachable_since >= UNREACHABLE_ISSUE_AFTER.total_seconds():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="unreachable",
                translation_placeholders={
                    "name": self.config_entry.title,
                    "host": self.client.host,
                },
            )
        # Hold the last known state through short blips.
        if self.data is not None and self.consecutive_failures < FAILURE_GRACE_POLLS:
            return self.data
        if self.data is not None and self.consecutive_failures == FAILURE_GRACE_POLLS:
            self.drops += 1
            self.last_drop = dt_util.utcnow()
        raise UpdateFailed(f"{self.client.host} unreachable: {error}")

    def _handle_success(self) -> None:
        if self.unreachable_since is not None:
            _LOGGER.info(
                "%s reachable again after %d failed polls",
                self.client.host,
                self.consecutive_failures,
            )
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        self.consecutive_failures = 0
        self.unreachable_since = None
        self.last_error = None

    def apply_status(self, **changes: object) -> None:
        """Optimistically reflect a control write until the next status read."""
        if self.data is None or self.data.status is None:
            return
        self._status = replace(self.data.status, **changes)  # type: ignore[arg-type]
        self.async_set_updated_data(replace(self.data, status=self._status))
