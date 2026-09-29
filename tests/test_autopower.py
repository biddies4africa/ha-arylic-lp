"""End-to-end auto-power behaviour with a fake player and an input_boolean plug."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.arylic_lp.const import CONF_AIRPLAY_FALLBACK, CONF_POWER_SWITCH, DOMAIN

PLUG = "input_boolean.plug"
PLAYING = "binary_sensor.lounge_speakers_playing"
STATUS = "sensor.lounge_speakers_auto_power"
DELAY = "number.lounge_speakers_auto_off_delay"
ONLY_OWN = "switch.lounge_speakers_only_turn_off_if_auto_on_turned_it_on"
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
