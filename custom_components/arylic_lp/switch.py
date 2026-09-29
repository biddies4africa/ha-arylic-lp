"""Switches: player settings (LED, virtual bass) and auto-power settings."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LPConfigEntry
from .autopower import AutoPowerController
from .client import LPClient
from .entity import LPConfigEntity, LPEntity

# Commands go over one serialized connection anyway.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class PlayerSwitchDescription(SwitchEntityDescription):
    field: str
    set: Callable[[LPClient, bool], Coroutine[Any, Any, None]]


PLAYER_SWITCHES: tuple[PlayerSwitchDescription, ...] = (
    PlayerSwitchDescription(
        key="led",
        translation_key="led",
        entity_category=EntityCategory.CONFIG,
        field="led",
        set=lambda client, on: client.set_led(on),
    ),
    PlayerSwitchDescription(
        key="virtual_bass",
        translation_key="virtual_bass",
        entity_category=EntityCategory.CONFIG,
        field="virtual_bass",
        set=lambda client, on: client.set_virtual_bass(on),
    ),
)


@dataclass(frozen=True, kw_only=True)
class SettingDescription(SwitchEntityDescription):
    default: bool
    get: Callable[[AutoPowerController], bool]
    set: Callable[[AutoPowerController, bool], None]


SETTINGS: tuple[SettingDescription, ...] = (
    SettingDescription(
        key="auto_on",
        translation_key="auto_on",
        default=True,
        get=lambda ap: ap.auto_on,
        set=lambda ap, v: ap.set_auto_on(v),
    ),
    SettingDescription(
        key="auto_off",
        translation_key="auto_off",
        default=True,
        get=lambda ap: ap.auto_off,
        set=lambda ap, v: ap.set_auto_off(v),
    ),
    SettingDescription(
        key="only_own",
        translation_key="only_own",
        default=False,
        get=lambda ap: ap.only_own,
        set=lambda ap, v: ap.set_only_own(v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    entities: list[SwitchEntity] = [PlayerSwitch(entry, d) for d in PLAYER_SWITCHES]
    if entry.runtime_data.autopower is not None:
        entities.extend(AutoPowerSettingSwitch(entry, d) for d in SETTINGS)
    async_add_entities(entities)


class PlayerSwitch(LPEntity, SwitchEntity):
    """An on/off setting stored on the player."""

    entity_description: PlayerSwitchDescription

    def __init__(self, entry: LPConfigEntry, description: PlayerSwitchDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        return getattr(self.status, self.entity_description.field) if self.status else None

    async def _async_set(self, on: bool) -> None:
        description = self.entity_description
        await self._async_command(
            f"turn {'on' if on else 'off'} {description.key.replace('_', ' ')}",
            description.set(self._client, on),
        )
        self.coordinator.apply_status(**{description.field: on})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)


class AutoPowerSettingSwitch(LPConfigEntity, SwitchEntity, RestoreEntity):
    """A persisted on/off auto-power setting."""

    entity_description: SettingDescription
    _attr_should_poll = False

    def __init__(self, entry: LPConfigEntry, description: SettingDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        value = self.entity_description.default
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            value = last.state == STATE_ON
        self.entity_description.set(self._autopower, value)

    @property
    def is_on(self) -> bool:
        return self.entity_description.get(self._autopower)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.entity_description.set(self._autopower, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.entity_description.set(self._autopower, False)
        self.async_write_ha_state()
