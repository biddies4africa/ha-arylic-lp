"""Diagnostics leave out addresses and device IDs (they get pasted into public issues)."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arylic_lp.const import CONF_AIRPLAY_FALLBACK, CONF_POWER_SWITCH, DOMAIN
from custom_components.arylic_lp.diagnostics import async_get_config_entry_diagnostics

HOST = "192.0.2.10"
PLUG_HOST = "192.0.2.77"
USN = "a0b1c2d3e4f5"


async def test_diagnostics_withhold_addresses(hass: HomeAssistant, patch_device) -> None:
    hass.states.async_set("switch.plug", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lounge Speakers",
        unique_id=USN,
        data={
            CONF_HOST: HOST,
            "identity": {
                "usn": USN,
                "devicename": "Lounge Speakers",
                "fwversion": "AR241CP_8285.9.1",
                "cast_model": "LP10",
                "source_list": "LS8::01000030",
                "host": HOST,
                "cast_timezone": "Europe/Paris",
                "ssid": "Home WiFi",
            },
        },
        options={CONF_POWER_SWITCH: "switch.plug", CONF_AIRPLAY_FALLBACK: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    entry.runtime_data.coordinator.last_error = f"{HOST} did not answer PLA;"
    entry.runtime_data.autopower.last_error = f"Cannot connect to {PLUG_HOST}"

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    for secret in (HOST, PLUG_HOST, USN, "Europe/Paris", "Home WiFi"):
        assert secret not in str(diagnostics)
    assert diagnostics["entry"]["data"]["identity"] == {
        "devicename": "Lounge Speakers",
        "cast_model": "LP10",
        "fwversion": "AR241CP_8285.9.1",
        "source_list": "LS8::01000030",
        "withheld_fields": ["cast_timezone", "host", "ssid", "usn"],
    }
    assert diagnostics["coordinator"]["last_error"] == "**REDACTED** did not answer PLA;"
    assert diagnostics["autopower"]["last_error"] == "Cannot connect to **REDACTED**"
