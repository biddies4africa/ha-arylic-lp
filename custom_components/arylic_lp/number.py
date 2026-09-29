"""Number entities: tone controls and the auto-off delay."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LPConfigEntry
from .client import TONE_MAX, TONE_MIN
from .const import DEFAULT_OFF_DELAY_MIN, MAX_OFF_DELAY_MIN, MIN_OFF_DELAY_MIN
from .entity import LPConfigEntity, LPEntity

# Commands go over one serialized connection anyway.
PARALLEL_UPDATES = 1

# Tone control -> (UART command, LPStatus field).
TONES: dict[str, tuple[str, str]] = {
    "bass": ("BAS", "bass"),
    "mid": ("MID", "mid"),
    "treble": ("TRE", "treble"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    entities: list[NumberEntity] = [ToneNumber(entry, key) for key in TONES]
    if entry.runtime_data.autopower is not None:
        entities.append(OffDelayNumber(entry))
    async_add_entities(entities)


class ToneNumber(LPEntity, NumberEntity):
    """Bass, mid or treble, -10..10 dB, stored on the player."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = TONE_MIN
    _attr_native_max_value = TONE_MAX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry: LPConfigEntry, key: str) -> None:
        super().__init__(entry, key)
        self._command, self._field = TONES[key]

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> int | None:
        return getattr(self.status, self._field) if self.status else None

    async def async_set_native_value(self, value: float) -> None:
        level = round(value)
        await self._async_command(f"set {self._field}", self._client.set_tone(self._command, level))
        self.coordinator.apply_status(**{self._field: level})


class OffDelayNumber(LPConfigEntity, RestoreNumber):
    """Minutes without playback before the speakers are switched off."""

    _attr_native_min_value = MIN_OFF_DELAY_MIN
    _attr_native_max_value = MAX_OFF_DELAY_MIN
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False

    def __init__(self, entry: LPConfigEntry) -> None:
        super().__init__(entry, "off_delay")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value = float(DEFAULT_OFF_DELAY_MIN)
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = min(max(last.native_value, MIN_OFF_DELAY_MIN), MAX_OFF_DELAY_MIN)
        self._autopower.set_off_delay(value)

    @property
    def native_value(self) -> int:
        # Whole minutes (step is 1), so the UI shows "30" rather than "30.0".
        return round(self._autopower.off_delay.total_seconds() / 60)

    async def async_set_native_value(self, value: float) -> None:
        self._autopower.set_off_delay(value)
        self.async_write_ha_state()
