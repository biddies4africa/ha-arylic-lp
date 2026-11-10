"""Player-backed entities: tone, LED, virtual bass, pushed frames, connection drops."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.arylic_lp.const import CONF_AIRPLAY_FALLBACK, CONF_POWER_SWITCH, DOMAIN

PLAYER = "media_player.lounge_speakers"
PLAYING = "binary_sensor.lounge_speakers_playing"
BASS = "number.lounge_speakers_eq_bass"
MID = "number.lounge_speakers_eq_mid"
TREBLE = "number.lounge_speakers_eq_treble"
LED = "switch.lounge_speakers_led"
VIRTUAL_BASS = "switch.lounge_speakers_eq_virtual_bass"
DROPS = "sensor.lounge_speakers_connection_drops"
PLUG_SELECT = "select.lounge_speakers_auto_power_plug"
AIRPLAY = "switch.lounge_speakers_airplay_playback_detection"


@pytest.fixture
async def player(hass: HomeAssistant, patch_device):
    """A player set up without a power switch (no auto-power entities)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lounge Speakers",
        unique_id="a0b1c2d3e4f5",
        data={CONF_HOST: "192.0.2.10"},
        options={CONF_AIRPLAY_FALLBACK: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return patch_device


async def _until(hass: HomeAssistant, condition: Callable[[], bool]) -> None:
    """Let the client's reader task process frames until condition holds."""
    for _ in range(200):
        await hass.async_block_till_done()
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


async def _advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_device_entities_without_plug(hass: HomeAssistant, player) -> None:
    assert hass.states.get(BASS).state == "0"
    assert hass.states.get(MID).state == "1"
    assert hass.states.get(TREBLE).state == "3"
    assert hass.states.get(LED).state == STATE_ON
    assert hass.states.get(VIRTUAL_BASS).state == STATE_OFF
    assert hass.states.get(DROPS).state == "0"
    # Auto-power entities only exist with a linked plug.
    assert hass.states.get("sensor.lounge_speakers_auto_power") is None
    assert hass.states.get("switch.lounge_speakers_auto_on") is None
    assert hass.states.get("number.lounge_speakers_auto_off_delay") is None


async def test_set_tone(hass: HomeAssistant, player) -> None:
    fake = player
    await hass.services.async_call(
        "number", "set_value", {"entity_id": TREBLE, "value": -4}, blocking=True
    )
    await _until(hass, lambda: fake.treble == -4)
    assert "TRE:-4" in fake.received
    assert hass.states.get(TREBLE).state == "-4"


async def test_led_and_virtual_bass(hass: HomeAssistant, player) -> None:
    fake = player
    await hass.services.async_call("switch", "turn_off", {"entity_id": LED}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": VIRTUAL_BASS}, blocking=True)
    await _until(hass, lambda: not fake.led and fake.virtual_bass)
    assert hass.states.get(LED).state == STATE_OFF
    assert hass.states.get(VIRTUAL_BASS).state == STATE_ON


async def test_pushed_frames_update_state_immediately(hass: HomeAssistant, player) -> None:
    fake = player
    # Changes made elsewhere (another app) arrive as frames; no poll is needed.
    await fake.push("VOL:55;")
    await _until(hass, lambda: hass.states.get(PLAYER).attributes.get("volume_level") == 0.55)
    await fake.push("SRC:BT;")
    await _until(hass, lambda: hass.states.get(PLAYER).attributes.get("source") == "Bluetooth")
    await fake.push("BAS:-2;")
    await _until(hass, lambda: hass.states.get(BASS).state == "-2")
    await fake.push("PLA:1;")
    await _until(hass, lambda: hass.states.get(PLAYING).state == STATE_ON)
    # Malformed or unknown frames are ignored.
    await fake.push("VOL:abc;XYZ:1;TRE:99;")
    await fake.push("BAS:-2;")
    await _until(hass, lambda: hass.states.get(BASS).state == "-2")
    assert hass.states.get(PLAYER).attributes["volume_level"] == 0.55
    assert hass.states.get(TREBLE).state == "3"


async def test_connection_drops_are_counted(hass: HomeAssistant, player, freezer) -> None:
    fake = player
    await fake.stop()
    for _ in range(5):
        await _advance(hass, freezer, 3)
    assert hass.states.get(PLAYING).state == STATE_UNAVAILABLE
    # The counter stays available while the player isn't.
    assert hass.states.get(DROPS).state == "1"
    assert hass.states.get(DROPS).attributes["last_drop"] is not None
    # A long outage is still one drop.
    for _ in range(20):
        await _advance(hass, freezer, 3)
    assert hass.states.get(DROPS).state == "1"

    await fake.restart()
    for _ in range(15):
        await _advance(hass, freezer, 3)
    assert hass.states.get(PLAYING).state != STATE_UNAVAILABLE
    await fake.stop()
    for _ in range(5):
        await _advance(hass, freezer, 3)
    assert hass.states.get(DROPS).state == "2"


async def test_unknown_source_is_rejected(hass: HomeAssistant, player) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": PLAYER, "source": "Cassette"},
            blocking=True,
        )


async def test_link_plug_from_device_page(hass: HomeAssistant, player) -> None:
    """Picking a plug in the Auto-power plug select adds the auto-power entities."""
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": {"plug": {}}})
    select = hass.states.get(PLUG_SELECT)
    assert select.state == "none"
    options = select.attributes["options"]
    # This integration's own switches aren't offered as plugs.
    assert not any(option.startswith("Lounge Speakers") for option in options)

    await hass.services.async_call(
        "select", "select_option", {"entity_id": PLUG_SELECT, "option": "plug"}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_POWER_SWITCH] == "input_boolean.plug"
    assert hass.states.get(PLUG_SELECT).state == "plug"
    assert hass.states.get("sensor.lounge_speakers_auto_power").state == "idle"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": PLUG_SELECT, "option": "none"}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options[CONF_POWER_SWITCH] is None
    assert hass.states.get("sensor.lounge_speakers_auto_power") is None


async def test_airplay_detection_switch_applies_without_reload(hass: HomeAssistant, player) -> None:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    client = entry.runtime_data.client
    assert hass.states.get(AIRPLAY).state == STATE_OFF

    await hass.services.async_call("switch", "turn_on", {"entity_id": AIRPLAY}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(AIRPLAY).state == STATE_ON
    assert entry.options[CONF_AIRPLAY_FALLBACK] is True
    assert entry.runtime_data.coordinator.airplay_fallback is True
    assert entry.runtime_data.client is client  # applied live, not reloaded
