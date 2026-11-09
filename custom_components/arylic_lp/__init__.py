"""Arylic - LP Series: play-state, controls and speaker auto-power for Arylic LP-series players."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .autopower import ENTITY_KEYS, AutoPowerController, async_remove_store
from .client import LPClient
from .const import CONF_AIRPLAY_FALLBACK, CONF_POWER_SWITCH, DOMAIN
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
    if autopower is None:
        await _async_remove_autopower(hass, entry)
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


async def _async_remove_autopower(hass: HomeAssistant, entry: LPConfigEntry) -> None:
    """Remove the auto-power entities and saved state left by an unlinked plug."""
    registry = er.async_get(hass)
    prefix = entry.unique_id or entry.entry_id
    removed = False
    for platform, key in ENTITY_KEYS:
        if entity_id := registry.async_get_entity_id(platform, DOMAIN, f"{prefix}_{key}"):
            registry.async_remove(entity_id)
            removed = True
    if removed:
        await async_remove_store(hass, entry.entry_id)


async def _async_options_updated(hass: HomeAssistant, entry: LPConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: LPConfigEntry) -> None:
    """Delete the entry's saved auto-power state with it."""
    await async_remove_store(hass, entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LPConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded
