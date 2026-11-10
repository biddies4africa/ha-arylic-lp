"""Select: which plug auto-power drives, on the device page (also under Configure)."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_added_domain,
    async_track_state_removed_domain,
)

from . import LPConfigEntry
from .const import CONF_POWER_SWITCH, DOMAIN, POWER_DOMAINS
from .entity import device_info

_LOGGER = logging.getLogger(__name__)

# Changing the plug reloads the entry; nothing here talks to the player.
PARALLEL_UPDATES = 0

NO_PLUG = "none"
# Plugs appearing or disappearing (all at once at startup) refresh the list
# once things settle, not once per entity.
REFRESH_COOLDOWN = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PowerSwitchSelect(entry)])


class PowerSwitchSelect(SelectEntity):
    """The plug (any switch, light or input_boolean) auto-power switches.

    Offered: entities a person would recognise as the thing itself, so not
    other integrations' settings or diagnostics, hidden or disabled entities,
    or this integration's own switches. The linked one is always listed.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "power_switch"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, entry: LPConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_power_switch"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        debouncer = Debouncer(
            self.hass,
            _LOGGER,
            cooldown=REFRESH_COOLDOWN,
            immediate=False,
            function=self.async_write_ha_state,
        )
        self.async_on_remove(debouncer.async_shutdown)

        @callback
        def candidates_changed(_event: Event[EventStateChangedData]) -> None:
            debouncer.async_schedule_call()

        for track in (async_track_state_added_domain, async_track_state_removed_domain):
            self.async_on_remove(track(self.hass, POWER_DOMAINS, candidates_changed))

    @property
    def _linked(self) -> str | None:
        return self._entry.options.get(CONF_POWER_SWITCH) or None

    def _choices(self) -> dict[str, str]:
        """Option label -> entity ID, sorted by label."""
        registry = er.async_get(self.hass)
        linked = self._linked
        by_name: dict[str, list[str]] = {}
        for state in self.hass.states.async_all(POWER_DOMAINS):
            entry = registry.async_get(state.entity_id)
            if (
                state.entity_id != linked
                and entry is not None
                and (
                    entry.platform == DOMAIN
                    or entry.entity_category is not None
                    or entry.hidden_by is not None
                    or entry.disabled_by is not None
                )
            ):
                continue
            by_name.setdefault(state.name, []).append(state.entity_id)
        if linked and self.hass.states.get(linked) is None:
            by_name.setdefault(linked, []).append(linked)  # linked but gone: show its ID
        choices = {
            name if len(ids) == 1 else f"{name} ({entity_id})": entity_id
            for name, ids in by_name.items()
            for entity_id in ids
        }
        return dict(sorted(choices.items(), key=lambda item: item[0].casefold()))

    @property
    def options(self) -> list[str]:
        return [NO_PLUG, *self._choices()]

    @property
    def current_option(self) -> str:
        if (linked := self._linked) is None:
            return NO_PLUG
        labels = {entity_id: label for label, entity_id in self._choices().items()}
        return labels.get(linked, linked)

    async def async_select_option(self, option: str) -> None:
        power_switch = None if option == NO_PLUG else self._choices()[option]
        # The entry reloads to add or remove the auto-power entities.
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_POWER_SWITCH: power_switch}
        )
