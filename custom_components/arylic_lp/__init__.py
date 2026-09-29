"""Arylic - LP Series: play-state, controls and speaker auto-power for Arylic LP-series players."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .autopower import AutoPowerController
from .client import LPClient
from .const import CONF_AIRPLAY_FALLBACK, CONF_POWER_SWITCH
from .coordinator import LPCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class LPData:
    """Runtime objects for one config entry."""

    client: LPClient
    coordinator: LPCoordinator
    autopower: AutoPowerController | None
    identity: dict[str, Any] = field(default_factory=dict)


type LPConfigEntry = ConfigEntry[LPData]


async def async_setup_entry(hass: HomeAssistant, entry: LPConfigEntry) -> bool:
    client = LPClient(entry.data[CONF_HOST])
    coordinator = LPCoordinator(
        hass,
        entry,
        client,
        airplay_fallback=entry.options.get(CONF_AIRPLAY_FALLBACK, True),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise

    power_switch = entry.options.get(CONF_POWER_SWITCH)
    autopower = (
        AutoPowerController(hass, entry, coordinator, power_switch) if power_switch else None
    )
    entry.runtime_data = LPData(
        client=client,
        coordinator=coordinator,
        autopower=autopower,
        identity=dict(entry.data.get("identity", {})),
    )

    # Entities restore the auto-power settings before the controller starts.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if autopower is not None:
        await autopower.async_start()
        entry.async_on_unload(autopower.async_stop)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: LPConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LPConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded
