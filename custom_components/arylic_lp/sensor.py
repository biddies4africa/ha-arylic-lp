"""Sensors: auto-power status and a connection-drop counter."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LPConfigEntry
from .autopower import STATUSES
from .entity import LPEntity, device_info

# Read-only; state comes from the coordinator.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    entities: list[SensorEntity] = [ConnectionDropsSensor(entry)]
    if entry.runtime_data.autopower is not None:
        entities.append(AutoPowerStatusSensor(entry))
    async_add_entities(entities)


class ConnectionDropsSensor(LPEntity, RestoreSensor):
    """How many times the player has become unreachable (kept across restarts)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, entry: LPConfigEntry) -> None:
        super().__init__(entry, "connection_drops")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self.coordinator
        if (last := await self.async_get_last_sensor_data()) is not None and isinstance(
            last.native_value, int | float
        ):
            coordinator.drops += int(last.native_value)
        if (
            state := await self.async_get_last_state()
        ) is not None and coordinator.last_drop is None:
            coordinator.last_drop = dt_util.parse_datetime(
                str(state.attributes.get("last_drop") or "")
            )

    @property
    def available(self) -> bool:
        # Most useful exactly while the player is unreachable.
        return True

    @property
    def native_value(self) -> int:
        return self.coordinator.drops

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last_drop = self.coordinator.last_drop
        return {"last_drop": last_drop.isoformat() if last_drop else None}


class AutoPowerStatusSensor(SensorEntity):
    """What auto-power is doing: playing, idle, off_pending, player_unavailable."""

    _attr_has_entity_name = True
    _attr_translation_key = "autopower_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES
    _attr_should_poll = False

    def __init__(self, entry: LPConfigEntry) -> None:
        assert entry.runtime_data.autopower is not None
        self._autopower = entry.runtime_data.autopower
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_autopower_status"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._autopower.add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        return self._autopower.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ap = self._autopower
        return {
            "power_switch": ap.power_switch,
            "turns_off_at": ap.off_at.isoformat() if ap.off_at else None,
            "turned_on_by_autopower": ap.armed,
            "last_action": ap.last_action,
            "last_action_at": ap.last_action_at.isoformat() if ap.last_action_at else None,
            "last_error": ap.last_error,
        }
