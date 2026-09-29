"""Playing sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LPConfigEntry
from .entity import LPEntity

# Read-only; state comes from the coordinator.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([LPPlayingSensor(entry)])


class LPPlayingSensor(LPEntity, BinarySensorEntity):
    """On while the player is playing from any source."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, entry: LPConfigEntry) -> None:
        super().__init__(entry, "playing")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.playing

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "mcu_play_flag": data.pla,
            "airplay_session": data.airplay,
            "source": data.status.source if data.status else None,
        }
