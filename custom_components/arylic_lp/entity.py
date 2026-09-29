"""Shared entity base."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LPConfigEntry
from .client import LPError, LPStatus
from .const import DOMAIN
from .coordinator import LPCoordinator


def device_info(entry: LPConfigEntry) -> DeviceInfo:
    identity = entry.runtime_data.identity
    unique_id = entry.unique_id or entry.entry_id
    return DeviceInfo(
        identifiers={(DOMAIN, unique_id)},
        name=entry.title,
        manufacturer="Arylic",
        model=identity.get("cast_model") or "LP series",
        sw_version=identity.get("fwversion"),
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )


class LPConfigEntity(Entity):
    """An auto-power setting, kept by the integration rather than the player."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: LPConfigEntry, key: str) -> None:
        assert entry.runtime_data.autopower is not None
        self._autopower = entry.runtime_data.autopower
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)
        self._attr_translation_key = key


class LPEntity(CoordinatorEntity[LPCoordinator]):
    """An entity backed by the player's coordinator."""

    _attr_has_entity_name = True

    def __init__(self, entry: LPConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._client = entry.runtime_data.client
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)
        self._attr_translation_key = key

    @property
    def status(self) -> LPStatus | None:
        return self.coordinator.data.status

    async def _async_command(self, action: str, command: Awaitable[None]) -> None:
        """Send a command to the player, as a translated error if it fails."""
        try:
            await command
        except LPError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"action": action, "error": str(err)},
            ) from err
