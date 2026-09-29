"""Config and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arylic_lp.client import LPConnectionError
from custom_components.arylic_lp.const import CONF_AIRPLAY_FALLBACK, CONF_POWER_SWITCH, DOMAIN


async def test_user_flow_creates_entry(hass: HomeAssistant, patch_device) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.0.2.10"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "power"
    with patch("custom_components.arylic_lp.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_POWER_SWITCH: "switch.lounge_speakers", CONF_AIRPLAY_FALLBACK: True},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Lounge Speakers"
    assert result["data"][CONF_HOST] == "192.0.2.10"
    assert result["options"][CONF_POWER_SWITCH] == "switch.lounge_speakers"
    assert result["result"].unique_id == "a0b1c2d3e4f5"


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.arylic_lp.config_flow.LPClient.get_playing",
        side_effect=LPConnectionError("nope"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.0.2.99"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_player_new_ip_updates_entry(hass: HomeAssistant, patch_device) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="a0b1c2d3e4f5", data={CONF_HOST: "192.0.2.5"})
    entry.add_to_hass(hass)
    with patch("custom_components.arylic_lp.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.0.2.10"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.10"


async def test_options_flow(hass: HomeAssistant, patch_device) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a0b1c2d3e4f5",
        data={CONF_HOST: "192.0.2.10"},
        options={CONF_POWER_SWITCH: None, CONF_AIRPLAY_FALLBACK: True},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.arylic_lp.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_POWER_SWITCH: "switch.office_speakers", CONF_AIRPLAY_FALLBACK: False},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_POWER_SWITCH: "switch.office_speakers",
        CONF_AIRPLAY_FALLBACK: False,
    }
