"""Auto-power: switch a speaker plug on when the player plays, off after idling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
    split_entity_id,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_OFF_DELAY_MIN,
    DOMAIN,
    EVENT_AUTOPOWER,
    POWER_CALL_TIMEOUT,
    POWER_CONFIRM_SECONDS,
    POWER_RETRY_DELAYS,
    POWER_RETRY_WINDOW,
    UNREACHABLE_OFF_DELAY,
)
from .coordinator import LPCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

STATUS_PLAYING = "playing"
STATUS_IDLE = "idle"
STATUS_OFF_PENDING = "off_pending"
STATUS_UNAVAILABLE = "player_unavailable"
STATUSES = [STATUS_PLAYING, STATUS_IDLE, STATUS_OFF_PENDING, STATUS_UNAVAILABLE]

# Why a power action was taken (event data "reason").
REASON_PLAYBACK_STARTED = "playback_started"
REASON_IDLE = "idle"
REASON_UNREACHABLE = "player_unreachable"

# Event data "result" values.
RESULT_REQUESTED = "requested"
RESULT_ATTEMPT_FAILED = "attempt_failed"
RESULT_SUCCEEDED = "succeeded"
RESULT_GAVE_UP = "gave_up"
RESULT_CANCELLED = "cancelled"


class AutoPowerController:
    """Drive one power switch from one player's play state.

    * Playing and the switch is off -> turn it on (if auto-on is enabled) and
      remember that we did ("armed").
    * Not playing while the switch is on -> turn it off after the delay (if
      auto-off is enabled). An unreachable player counts as not playing, but
      with a countdown of at least UNREACHABLE_OFF_DELAY. With "only turn off
      what we turned on", a manually switched-on plug is left alone.
    * Switch commands are confirmed against the switch's state and retried with
      backoff. This works with any switch entity: success is the service call
      returning without error *and* the state reaching the target.
    * Each action is fired as an EVENT_AUTOPOWER event, which the logbook shows.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: LPCoordinator,
        power_switch: str,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.power_switch = power_switch
        self.auto_on = True
        self.auto_off = True
        self.only_own = False
        self.off_delay = timedelta(minutes=DEFAULT_OFF_DELAY_MIN)
        self.armed = False
        self.off_at: datetime | None = None
        self.last_action: str | None = None
        self.last_action_at: datetime | None = None
        self.last_error: str | None = None
        # Last play state actually read from the player; None until one is.
        self._was_playing: bool | None = None
        # Whether the running countdown was started while the player was unreachable.
        self._off_timer_unreachable = False
        self._started = False
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._lock = asyncio.Lock()
        self._off_timer: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    @property
    def issue_id(self) -> str:
        return f"power_failed_{self.entry.entry_id}"

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self.armed = bool(stored.get("armed", False))
        # Already playing at startup is not a "start": don't override a plug
        # that was switched off before a restart.
        self._was_playing = self.playing if self.player_available else None
        self._started = True
        self._unsubs.append(self.coordinator.async_add_listener(self._evaluate))
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self.power_switch], self._switch_changed)
        )
        self._evaluate()

    @callback
    def async_stop(self) -> None:
        self._started = False
        self._cancel_off_timer()
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # Settings (set by the config entities, which restore them on startup)

    @callback
    def set_auto_on(self, value: bool) -> None:
        self.auto_on = value
        self._evaluate()

    @callback
    def set_auto_off(self, value: bool) -> None:
        self.auto_off = value
        self._evaluate()

    @callback
    def set_only_own(self, value: bool) -> None:
        self.only_own = value
        self._evaluate()

    @callback
    def set_off_delay(self, minutes: float) -> None:
        self.off_delay = timedelta(minutes=minutes)
        # Restart any running countdown with the new delay.
        self._cancel_off_timer()
        self._evaluate()

    # State

    @property
    def player_available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def playing(self) -> bool:
        return self.player_available and self.coordinator.data.playing

    @property
    def status(self) -> str:
        if self.playing:
            return STATUS_PLAYING
        if self._off_timer is not None:
            return STATUS_OFF_PENDING
        if not self.player_available:
            return STATUS_UNAVAILABLE
        return STATUS_IDLE

    def _switch_state(self) -> str | None:
        state = self.hass.states.get(self.power_switch)
        return state.state if state else None

    @callback
    def _switch_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is not None and new.state == STATE_OFF and self.armed:
            self._set_armed(False)
        self._evaluate()

    @callback
    def _set_armed(self, value: bool) -> None:
        if self.armed != value:
            self.armed = value
            self._store.async_delay_save(lambda: {"armed": self.armed}, 1)

    @property
    def _may_turn_off(self) -> bool:
        return self.auto_off and (self.armed or not self.only_own)

    @callback
    def _evaluate(self) -> None:
        if not self._started:
            return
        switch = self._switch_state()
        available = self.player_available
        playing = self.playing
        # Turn on only when playback *starts*, so switching the plug off by
        # hand mid-song is respected. Only a stopped -> playing change read from
        # the player counts: an outage leaves the last state as it was, so the
        # player coming back still playing is not a start.
        started = playing and self._was_playing is False
        if available:
            self._was_playing = playing
        if playing:
            self._cancel_off_timer()
            if started and self.auto_on and switch == STATE_OFF:
                self._run(self._async_set_power(True, REASON_PLAYBACK_STARTED))
        elif not self._may_turn_off or switch == STATE_OFF:
            self._cancel_off_timer()
        else:
            if available and self._off_timer_unreachable:
                # Back and not playing: count down from now at the normal delay.
                self._cancel_off_timer()
            if self._off_timer is None and switch == STATE_ON:
                # An unavailable switch keeps a running countdown but doesn't start one.
                delay = self.off_delay
                if not available:
                    delay = max(delay, UNREACHABLE_OFF_DELAY)
                self._off_timer_unreachable = not available
                self.off_at = dt_util.utcnow() + delay
                self._off_timer = async_call_later(self.hass, delay, self._off_timer_fired)
        self._notify()

    @callback
    def _cancel_off_timer(self) -> None:
        if self._off_timer is not None:
            self._off_timer()
            self._off_timer = None
        self._off_timer_unreachable = False
        self.off_at = None

    @callback
    def _off_timer_fired(self, _now: datetime) -> None:
        reason = REASON_UNREACHABLE if self._off_timer_unreachable else REASON_IDLE
        self._off_timer = None
        self._off_timer_unreachable = False
        self.off_at = None
        if not self.playing and self._may_turn_off and self._switch_state() != STATE_OFF:
            self._run(self._async_set_power(False, reason))
        self._notify()

    def _run(self, coro: Any) -> None:
        self.entry.async_create_background_task(
            self.hass, coro, f"{DOMAIN} power {self.power_switch}"
        )

    def _cancel_reason(self, turn_on: bool) -> str | None:
        """Why the action is no longer wanted, or None if it still is."""
        if turn_on:
            if not self.playing:
                return "playback stopped"
            if not self.auto_on:
                return "auto-on was switched off"
        else:
            if self.playing:
                return "playback started"
            if not self._may_turn_off:
                return "auto-off no longer applies"
        return None

    async def _async_set_power(self, turn_on: bool, reason: str) -> None:
        async with self._lock:
            target = STATE_ON if turn_on else STATE_OFF
            service = "turn_on" if turn_on else "turn_off"
            context = Context()
            error = "unknown"
            loop = asyncio.get_running_loop()
            deadline = loop.time() + POWER_RETRY_WINDOW
            # Attempt 1 goes straight away; each retry waits its delay first,
            # and none starts unless it fits in the window.
            for attempt, delay in enumerate((0, *POWER_RETRY_DELAYS), start=1):
                if loop.time() + delay >= deadline:
                    attempt -= 1
                    break
                if delay:
                    await asyncio.sleep(delay)
                # Re-check before every command: conditions may have changed
                # while queued or waiting, and a failed call may still have
                # switched the plug (or someone switched it by hand).
                if (cancelled := self._cancel_reason(turn_on)) is not None:
                    if attempt > 1:
                        self._fire(
                            service,
                            RESULT_CANCELLED,
                            reason,
                            context,
                            attempt=attempt - 1,
                            error=cancelled,
                        )
                    return
                if self._switch_state() == target:
                    self._set_armed(turn_on)
                    if attempt > 1:
                        self._succeeded(service, reason, context, attempt - 1)
                    return
                if attempt == 1:
                    self._fire(service, RESULT_REQUESTED, reason, context)

                call_timeout = min(POWER_CALL_TIMEOUT, deadline - loop.time())
                try:
                    async with asyncio.timeout(call_timeout):
                        await self.hass.services.async_call(
                            split_entity_id(self.power_switch)[0],
                            service,
                            {"entity_id": self.power_switch},
                            blocking=True,
                            context=context,
                        )
                except TimeoutError:
                    error = "no response"
                except Exception as err:
                    # Integrations should raise HomeAssistantError, but some
                    # leak their library's own exceptions (Tuya does).
                    error = str(err) or type(err).__name__
                else:
                    confirm = min(POWER_CONFIRM_SECONDS, max(deadline - loop.time(), 1))
                    if await self._async_wait_for_state(target, confirm):
                        self._succeeded(service, reason, context, attempt)
                        return
                    error = f"still {self._switch_state()}"

                _LOGGER.warning(
                    "%s %s failed (attempt %d): %s", service, self.power_switch, attempt, error
                )
                self._record_action(service, error)
                self._fire(
                    service, RESULT_ATTEMPT_FAILED, reason, context, attempt=attempt, error=error
                )

            _LOGGER.warning("Gave up trying to %s %s", service, self.power_switch)
            self._fire(service, RESULT_GAVE_UP, reason, context, attempt=attempt, error=error)
            self._create_issue(service, error)

    @callback
    def _succeeded(self, service: str, reason: str, context: Context, attempt: int) -> None:
        self._record_action(service, None)
        self._set_armed(service == "turn_on")
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        # A first-time success shows in the logbook as the switch's own state
        # change, attributed to the "requested" entry through the shared context.
        if attempt > 1:
            self._fire(service, RESULT_SUCCEEDED, reason, context, attempt=attempt)

    @callback
    def _create_issue(self, service: str, error: str) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="power_failed",
            translation_placeholders={
                "switch": self.power_switch,
                "action": service.replace("_", " "),
                "error": error,
            },
        )

    @callback
    def _fire(
        self,
        service: str,
        result: str,
        reason: str,
        context: Context,
        **extra: Any,
    ) -> None:
        """Fire an EVENT_AUTOPOWER event (shown in the logbook by logbook.py)."""
        status_entity = er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{self.entry.unique_id or self.entry.entry_id}_autopower_status"
        )
        switch_state = self.hass.states.get(self.power_switch)
        self.hass.bus.async_fire(
            EVENT_AUTOPOWER,
            {
                "entity_id": status_entity,
                "power_switch": self.power_switch,
                "power_switch_name": switch_state.name if switch_state else self.power_switch,
                "action": service,
                "result": result,
                "reason": reason,
                **extra,
            },
            context=context,
        )

    async def _async_wait_for_state(self, target: str, seconds: float) -> bool:
        for _ in range(int(seconds * 2)):
            if self._switch_state() == target:
                return True
            await asyncio.sleep(0.5)
        return self._switch_state() == target

    @callback
    def _record_action(self, service: str, error: str | None) -> None:
        self.last_action = service
        self.last_action_at = dt_util.utcnow()
        self.last_error = error
        self._notify()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "power_switch": self.power_switch,
            "auto_on": self.auto_on,
            "auto_off": self.auto_off,
            "only_own": self.only_own,
            "off_delay_min": self.off_delay.total_seconds() / 60,
            "armed": self.armed,
            "status": self.status,
            "off_at": self.off_at.isoformat() if self.off_at else None,
            "last_action": self.last_action,
            "last_action_at": self.last_action_at.isoformat() if self.last_action_at else None,
            "last_error": self.last_error,
        }
