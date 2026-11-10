"""End-to-end auto-power behaviour with a fake player and an input_boolean plug."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import (
    CONF_HOST,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.arylic_lp.const import (
    CONF_AIRPLAY_FALLBACK,
    CONF_POWER_SWITCH,
    DOMAIN,
    EVENT_AUTOPOWER,
)

PLUG = "input_boolean.plug"
PLAYING = "binary_sensor.lounge_speakers_playing"
STATUS = "sensor.lounge_speakers_auto_power"
DELAY = "number.lounge_speakers_auto_off_delay"
ONLY_OWN = "switch.lounge_speakers_auto_off_only_if_auto_on_turned_it_on"
PLAYER = "media_player.lounge_speakers"


@pytest.fixture
async def setup(hass: HomeAssistant, patch_device):
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"plug": {}}})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lounge Speakers",
        unique_id="a0b1c2d3e4f5",
        data={CONF_HOST: "192.0.2.10"},
        options={CONF_POWER_SWITCH: PLUG, CONF_AIRPLAY_FALLBACK: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return patch_device, entry


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_entities_created(hass: HomeAssistant, setup) -> None:
    assert hass.states.get(PLAYING).state == STATE_OFF
    assert hass.states.get(STATUS).state == "idle"
    assert hass.states.get(DELAY).state == "30"
    assert hass.states.get(PLAYER).attributes["volume_level"] == 0.24


async def test_configuration_entities_are_grouped(hass: HomeAssistant, setup) -> None:
    """The device page lists entities by name, so the names set the grouping."""
    names = [
        hass.states.get(entry.entity_id).name.removeprefix("Lounge Speakers ")
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN and entry.entity_category is EntityCategory.CONFIG
    ]
    assert sorted(names, key=str.casefold) == [
        "Auto-off",
        "Auto-off delay",
        "Auto-off only if auto-on turned it on",
        "Auto-on",
        "EQ bass",
        "EQ mid",
        "EQ treble",
        "EQ virtual bass",
        "LED",
    ]


async def test_turns_on_when_playback_starts_and_off_after_delay(
    hass: HomeAssistant, setup, freezer
) -> None:
    fake, _ = setup
    fake.playing = True
    await _advance(hass, freezer, 3)
    assert hass.states.get(PLAYING).state == STATE_ON
    assert hass.states.get(PLUG).state == STATE_ON

    fake.playing = False
    await _advance(hass, freezer, 3)
    assert hass.states.get(STATUS).state == "off_pending"
    await _advance(hass, freezer, 29 * 60)
    assert hass.states.get(PLUG).state == STATE_ON
    await _advance(hass, freezer, 2 * 60)
    assert hass.states.get(PLUG).state == STATE_OFF
    assert hass.states.get(STATUS).state == "idle"


async def test_resuming_cancels_off_timer(hass: HomeAssistant, setup, freezer) -> None:
    fake, _ = setup
    fake.playing = True
    await _advance(hass, freezer, 3)
    fake.playing = False
    await _advance(hass, freezer, 10 * 60)
    fake.playing = True
    await _advance(hass, freezer, 3)
    assert hass.states.get(STATUS).state == "playing"
    await _advance(hass, freezer, 60 * 60)
    assert hass.states.get(PLUG).state == STATE_ON


async def test_manual_off_while_playing_is_respected(hass: HomeAssistant, setup, freezer) -> None:
    fake, _ = setup
    fake.playing = True
    await _advance(hass, freezer, 3)
    await hass.services.async_call("input_boolean", "turn_off", {"entity_id": PLUG}, blocking=True)
    await _advance(hass, freezer, 3)
    await _advance(hass, freezer, 3)
    assert hass.states.get(PLUG).state == STATE_OFF


async def test_only_own_leaves_manual_plug_on(hass: HomeAssistant, setup, freezer) -> None:
    await hass.services.async_call("switch", "turn_on", {"entity_id": ONLY_OWN}, blocking=True)
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": PLUG}, blocking=True)
    await _advance(hass, freezer, 3)
    await _advance(hass, freezer, 2 * 60 * 60)
    assert hass.states.get(PLUG).state == STATE_ON


async def test_without_only_own_manual_plug_turns_off(hass: HomeAssistant, setup, freezer) -> None:
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": PLUG}, blocking=True)
    await _advance(hass, freezer, 3)
    await _advance(hass, freezer, 31 * 60)
    assert hass.states.get(PLUG).state == STATE_OFF


async def test_short_blip_holds_state_then_unavailable(hass: HomeAssistant, setup, freezer) -> None:
    fake, _ = setup
    fake.playing = True
    await _advance(hass, freezer, 3)
    await fake.stop()  # the player goes away entirely
    await _advance(hass, freezer, 3)
    # Within the grace window the last state is held.
    assert hass.states.get(PLAYING).state == STATE_ON
    for _ in range(10):
        await _advance(hass, freezer, 3)
    assert hass.states.get(PLAYING).state == STATE_UNAVAILABLE
    assert hass.states.get(STATUS).state == "off_pending"


async def _outage(hass: HomeAssistant, freezer, fake, seconds: float) -> None:
    """Take the player away for a while, then bring it back and let it reconnect."""
    await fake.stop()
    for _ in range(int(seconds // 3)):
        await _advance(hass, freezer, 3)
    assert hass.states.get(PLAYING).state == STATE_UNAVAILABLE
    await fake.restart()
    # Outlast the client's reconnect backoff.
    for _ in range(15):
        await _advance(hass, freezer, 3)
    assert hass.states.get(PLAYING).state != STATE_UNAVAILABLE


async def test_outage_does_not_turn_plug_off_early(hass: HomeAssistant, setup, freezer) -> None:
    fake, _ = setup
    await hass.services.async_call(
        "number", "set_value", {"entity_id": DELAY, "value": 2}, blocking=True
    )
    fake.playing = True
    await _advance(hass, freezer, 3)
    assert hass.states.get(PLUG).state == STATE_ON
    switched_on_at = hass.states.get(PLUG).last_changed
    # Longer than the 2 min delay, as the LP10's network restarts can be.
    await _outage(hass, freezer, fake, 150)
    # Not even briefly switched off and back on.
    assert hass.states.get(PLUG).last_changed == switched_on_at
    assert hass.states.get(STATUS).state == "playing"


async def test_player_back_still_playing_is_not_a_start(
    hass: HomeAssistant, setup, freezer
) -> None:
    fake, _ = setup
    fake.playing = True
    await _advance(hass, freezer, 3)
    await hass.services.async_call("input_boolean", "turn_off", {"entity_id": PLUG}, blocking=True)
    await _outage(hass, freezer, fake, 60)
    assert hass.states.get(PLUG).state == STATE_OFF


async def test_long_outage_turns_plug_off(hass: HomeAssistant, setup, freezer) -> None:
    fake, _ = setup
    fake.playing = True
    await _advance(hass, freezer, 3)
    await fake.stop()
    await _advance(hass, freezer, 3)
    for _ in range(10):
        await _advance(hass, freezer, 3)
    assert hass.states.get(STATUS).state == "off_pending"
    await _advance(hass, freezer, 58 * 60)
    assert hass.states.get(PLUG).state == STATE_ON
    await _advance(hass, freezer, 3 * 60)
    assert hass.states.get(PLUG).state == STATE_OFF


async def test_back_but_idle_counts_down_normal_delay(hass: HomeAssistant, setup, freezer) -> None:
    fake, _ = setup
    fake.playing = True
    await _advance(hass, freezer, 3)
    fake.playing = False
    await _outage(hass, freezer, fake, 60)
    assert hass.states.get(STATUS).state == "off_pending"
    await _advance(hass, freezer, 28 * 60)
    assert hass.states.get(PLUG).state == STATE_ON
    await _advance(hass, freezer, 3 * 60)
    assert hass.states.get(PLUG).state == STATE_OFF


async def test_media_player_controls(hass: HomeAssistant, setup) -> None:
    fake, entry = setup
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": PLAYER, "volume_level": 0.5}, blocking=True
    )
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": PLAYER, "source": "Bluetooth"}, blocking=True
    )
    # A query round-trip guarantees the fake has processed the earlier writes.
    await entry.runtime_data.client.get_playing()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert fake.volume == 50
    assert fake.source == "BT"
    assert hass.states.get(PLAYER).attributes["volume_level"] == 0.5
    assert hass.states.get(PLAYER).attributes["source"] == "Bluetooth"


async def test_unload(hass: HomeAssistant, setup) -> None:
    _, entry = setup
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


AUTOPOWER_ENTITIES = (
    STATUS,
    DELAY,
    ONLY_OWN,
    "switch.lounge_speakers_auto_on",
    "switch.lounge_speakers_auto_off",
)


async def test_unlinking_plug_removes_autopower(hass: HomeAssistant, setup, hass_storage) -> None:
    _, entry = setup
    registry = er.async_get(hass)
    for entity_id in AUTOPOWER_ENTITIES:
        assert registry.async_get(entity_id) is not None
    store_key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[store_key] = {"version": 1, "key": store_key, "data": {"armed": True}}

    hass.config_entries.async_update_entry(entry, options={CONF_AIRPLAY_FALLBACK: True})
    await hass.async_block_till_done(wait_background_tasks=True)

    for entity_id in AUTOPOWER_ENTITIES:
        assert registry.async_get(entity_id) is None
        assert hass.states.get(entity_id) is None
    assert hass.states.get(PLAYER) is not None
    assert store_key not in hass_storage


async def test_removing_entry_deletes_saved_state(hass: HomeAssistant, setup, hass_storage) -> None:
    _, entry = setup
    store_key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[store_key] = {"version": 1, "key": store_key, "data": {"armed": True}}

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert store_key not in hass_storage


class VendorError(Exception):
    """A library exception that isn't a HomeAssistantError (as Tuya raises)."""


@pytest.fixture
async def flaky_setup(hass: HomeAssistant, patch_device):
    """A plug whose turn_on fails `failures["count"]` times before working."""
    failures = {"count": 0, "calls": 0}
    hass.states.async_set("fakeplug.plug", STATE_OFF, {"friendly_name": "Fake Plug"})

    async def turn_on(call) -> None:
        failures["calls"] += 1
        if failures["calls"] <= failures["count"]:
            raise VendorError("network error:(2001) 2001")
        hass.states.async_set("fakeplug.plug", STATE_ON, {"friendly_name": "Fake Plug"})

    hass.services.async_register("fakeplug", "turn_on", turn_on)
    events = async_capture_events(hass, EVENT_AUTOPOWER)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lounge Speakers",
        unique_id="a0b1c2d3e4f5",
        data={CONF_HOST: "192.0.2.10"},
        options={CONF_POWER_SWITCH: "fakeplug.plug", CONF_AIRPLAY_FALLBACK: True},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.arylic_lp.autopower.POWER_RETRY_DELAYS", (0, 0, 0, 0)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        yield patch_device, failures, events


async def test_vendor_exception_is_retried(hass: HomeAssistant, flaky_setup, freezer) -> None:
    fake, failures, events = flaky_setup
    failures["count"] = 2
    fake.playing = True
    await _advance(hass, freezer, 3)
    assert hass.states.get("fakeplug.plug").state == STATE_ON
    assert failures["calls"] == 3
    assert [e.data["result"] for e in events] == [
        "requested",
        "attempt_failed",
        "attempt_failed",
        "succeeded",
    ]
    assert events[1].data["error"] == "network error:(2001) 2001"
    assert events[0].data["entity_id"] == STATUS
    status = hass.states.get(STATUS).attributes
    assert status["last_action"] == "turn_on"
    assert status["last_error"] is None
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"power_failed_{_entry_id(hass)}")


async def test_gives_up_and_raises_issue(hass: HomeAssistant, flaky_setup, freezer) -> None:
    fake, failures, events = flaky_setup
    failures["count"] = 99
    fake.playing = True
    await _advance(hass, freezer, 3)
    assert hass.states.get("fakeplug.plug").state == STATE_OFF
    assert failures["calls"] == 5  # first attempt + 4 retries
    assert events[-1].data["result"] == "gave_up"
    assert events[-1].data["attempt"] == 5
    assert hass.states.get(STATUS).attributes["last_error"] == "network error:(2001) 2001"
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"power_failed_{_entry_id(hass)}")


async def test_logbook_messages(hass: HomeAssistant, flaky_setup, freezer) -> None:
    from custom_components.arylic_lp.logbook import _message

    fake, failures, events = flaky_setup
    failures["count"] = 1
    fake.playing = True
    await _advance(hass, freezer, 3)
    messages = [_message(e.data) for e in events]
    assert messages == [
        "turning on Fake Plug (playback started)",
        "attempt 1 failed: network error:(2001) 2001",
        "turned on Fake Plug (attempt 2)",
    ]


def _entry_id(hass: HomeAssistant) -> str:
    return hass.config_entries.async_entries(DOMAIN)[0].entry_id


async def test_hung_call_gives_up_within_window(hass: HomeAssistant, flaky_setup, freezer) -> None:
    fake, _, events = flaky_setup
    autopower = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.autopower
    fake.playing = True
    autopower.set_auto_on(False)
    await _advance(hass, freezer, 3)
    autopower.set_auto_on(True)

    async def hang(call) -> None:
        await asyncio.Event().wait()

    hass.services.async_register("fakeplug", "turn_on", hang)
    task = hass.async_create_task(autopower._async_set_power(True, "playback_started"))
    # The (frozen) clock only moves when ticked; the window is 20 s.
    for _ in range(25):
        freezer.tick(timedelta(seconds=1))
        for _ in range(5):
            await asyncio.sleep(0)
    await task
    # One command in flight at a time, never a second while waiting; then give up.
    results = [e.data["result"] for e in events]
    assert results[0] == "requested"
    assert results[-1] == "gave_up"
    assert {e.data["error"] for e in events if e.data["result"] == "attempt_failed"} == {
        "no response"
    }
    assert hass.states.get("fakeplug.plug").state == STATE_OFF
