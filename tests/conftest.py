"""Shared fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


class FakePlayer:
    """A minimal TCP 2018 server speaking the Arylic UART text protocol."""

    def __init__(self) -> None:
        self.playing = False
        self.volume = 24
        self.muted = False
        self.source = "NET"
        self.treble = 3
        self.bass = 0
        self.mid = 1
        self.led = True
        self.virtual_bass = False
        self.received: list[str] = []
        self.respond = True
        self.broadcast_before_reply: str | None = None
        self.server: asyncio.Server | None = None
        self.connections = 0
        self._port = 0
        self._writers: list[asyncio.StreamWriter] = []

    @property
    def port(self) -> int:
        assert self.server is not None
        return self.server.sockets[0].getsockname()[1]

    def reply(self, command: str) -> str | None:
        if command == "PLA":
            return f"PLA:{int(self.playing)};"
        if command == "STA":
            return (
                f"STA:{self.source},{int(self.muted)},{self.volume},{self.treble},{self.bass},"
                f"3,0,{int(self.playing)},{int(self.led)},0;"
            )
        if command == "MID":
            return f"MID:{self.mid};"
        if command == "VBS":
            return f"VBS:{int(self.virtual_bass)};"
        name, _, value = command.partition(":")
        if name == "VOL":
            self.volume = int(value)
        elif name == "SRC":
            self.source = value
        elif name == "POP":
            self.playing = not self.playing
        # Like the real player, these settings echo their new value.
        elif name == "MUT":
            self.muted = value == "1"
            return f"MUT:{value};"
        elif name in ("TRE", "BAS", "MID"):
            setattr(self, {"TRE": "treble", "BAS": "bass", "MID": "mid"}[name], int(value))
            return f"{name}:{value};"
        elif name == "LED":
            self.led = value == "1"
            return f"LED:{value};"
        elif name == "VBS":
            self.virtual_bass = value == "1"
            return f"VBS:{value};"
        return None

    async def push(self, frame: str) -> None:
        """Send a frame to every client, as the player does after a change."""
        for writer in self._writers:
            writer.write(frame.encode())
            await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        buffer = b""
        try:
            while chunk := await reader.read(1024):
                buffer += chunk
                while b";" in buffer:
                    frame, _, buffer = buffer.partition(b";")
                    command = frame.decode()
                    self.received.append(command)
                    if not self.respond:
                        continue
                    if self.broadcast_before_reply:
                        writer.write(self.broadcast_before_reply.encode())
                    answer = self.reply(command)
                    if answer:
                        writer.write(answer.encode())
                    await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def start(self, port: int = 0) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self._port = self.port

    async def restart(self) -> None:
        """Come back on the same port after stop(), like the player after an outage."""
        await self.start(self._port)

    def drop_clients(self) -> None:
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    async def stop(self) -> None:
        self.drop_clients()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


@pytest.fixture
async def fake_player(socket_enabled: None) -> AsyncIterator[FakePlayer]:
    """A fake player on localhost (sockets are otherwise blocked in HA tests)."""
    fake = FakePlayer()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def patch_device(fake_player: FakePlayer) -> Iterator[FakePlayer]:
    """Route the integration's clients to the fake server; stub UDP/AirPlay."""
    from custom_components.arylic_lp.client import LPClient

    def _client(host: str, port: int = 2018) -> LPClient:
        return LPClient("127.0.0.1", fake_player.port)

    async def _airplay(host: str, timeout: float = 2.0) -> bool:
        return False

    identity = {
        "usn": "a0b1c2d3e4f5",
        "devicename": "Lounge Speakers",
        "fwversion": "AR241CP_8285.9.1",
        "cast_model": "LP10",
    }
    with (
        patch("custom_components.arylic_lp.LPClient", _client),
        patch("custom_components.arylic_lp.config_flow.LPClient", _client),
        patch("custom_components.arylic_lp.config_flow.discover", return_value=identity),
        patch("custom_components.arylic_lp.coordinator.async_airplay_active", _airplay),
    ):
        yield fake_player
