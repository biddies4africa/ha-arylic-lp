"""Media player: volume, mute, source and transport controls."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LPConfigEntry
from .const import DOMAIN
from .entity import LPEntity

# Commands go over one serialized connection anyway.
PARALLEL_UPDATES = 1

# Device source code -> display name.
SOURCE_NAMES: dict[str, str] = {
    "NET": "Network",
    "BT": "Bluetooth",
    "LINE-IN": "Line in",
    "USBPLAY": "USB",
}
SOURCE_CODES = {name: code for code, name in SOURCE_NAMES.items()}

VOLUME_STEP = 0.02


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([LPMediaPlayer(entry)])


class LPMediaPlayer(LPEntity, MediaPlayerEntity):
    """Controls over the player's TCP 2018 text API.

    The player does not report track metadata over this interface, and pause
    vs. stop are indistinguishable, so "not playing" is shown as idle.
    """

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )
    _attr_source_list = list(SOURCE_NAMES.values())
    _attr_volume_step = VOLUME_STEP

    def __init__(self, entry: LPConfigEntry) -> None:
        super().__init__(entry, "player")

    @property
    def state(self) -> MediaPlayerState:
        return MediaPlayerState.PLAYING if self.coordinator.data.playing else MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        return self.status.volume / 100 if self.status else None

    @property
    def is_volume_muted(self) -> bool | None:
        return self.status.muted if self.status else None

    @property
    def source(self) -> str | None:
        if self.status is None:
            return None
        return SOURCE_NAMES.get(self.status.source, self.status.source)

    async def async_set_volume_level(self, volume: float) -> None:
        level = round(max(0.0, min(1.0, volume)) * 100)
        await self._async_command("set volume", self._client.set_volume(level))
        self.coordinator.apply_status(volume=level)

    async def async_mute_volume(self, mute: bool) -> None:
        await self._async_command("mute", self._client.set_mute(mute))
        self.coordinator.apply_status(muted=mute)

    async def async_select_source(self, source: str) -> None:
        code = SOURCE_CODES.get(source)
        if code is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_source",
                translation_placeholders={"source": source},
            )
        await self._async_command("select source", self._client.set_source(code))
        self.coordinator.apply_status(source=code)
        self.coordinator.request_status_refresh()

    async def async_media_play(self) -> None:
        if not self.coordinator.data.playing:
            await self._async_command("play", self._client.play_pause())
            await self.coordinator.async_request_refresh()

    async def async_media_pause(self) -> None:
        if self.coordinator.data.playing:
            await self._async_command("pause", self._client.play_pause())
            await self.coordinator.async_request_refresh()

    async def async_media_next_track(self) -> None:
        await self._async_command("next track", self._client.next_track())

    async def async_media_previous_track(self) -> None:
        await self._async_command("previous track", self._client.previous_track())
