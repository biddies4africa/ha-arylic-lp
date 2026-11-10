"""Protocol client tests against a fake player."""

from __future__ import annotations

import asyncio
import plistlib
import socket
import threading
import time

import pytest

from custom_components.arylic_lp import client as client_mod
from custom_components.arylic_lp.client import (
    LPClient,
    LPConnectionError,
    LPProtocolError,
    async_airplay_active,
    device_id,
    discover,
    parse_play_state,
    parse_setting,
    parse_status,
)


def test_parse_status() -> None:
    status = parse_status("STA:NET,0,24,3,0,3,0,1,1,0")
    assert status.source == "NET"
    assert status.volume == 24
    assert status.muted is False
    assert status.playing is True


@pytest.mark.parametrize(
    "reply",
    [
        "STA:NET,0",
        "STA:NET,0,abc,3,0,3,0,0",
        "VOL:3",
        "STA:,0,24,3,0,3,0,0",
        f"STA:{'X' * 33},0,24,3,0,3,0,0",
        "STA:NET\x07,0,24,3,0,3,0,0",
    ],
)
def test_parse_status_rejects_garbage(reply: str) -> None:
    with pytest.raises(LPProtocolError):
        parse_status(reply)


def test_out_of_range_tone_reads_as_unknown() -> None:
    status = parse_status("STA:NET,0,24,99,-3,3,0,0,1,0")
    assert status.treble is None
    assert status.bass == -3


def test_source_frames_are_checked() -> None:
    assert parse_setting("SRC:LINE-IN") == ("source", "LINE-IN")
    assert parse_setting(f"SRC:{'X' * 33}") is None
    assert parse_setting("SRC:") is None


def test_parse_play_state() -> None:
    assert parse_play_state("PLA:1") is True
    assert parse_play_state("PLA:0") is False
    with pytest.raises(LPProtocolError):
        parse_play_state("PLA:x")


async def test_queries_share_one_connection(fake_player) -> None:
    client = LPClient("127.0.0.1", fake_player.port)
    assert await client.get_playing() is False
    fake_player.playing = True
    assert await client.get_playing() is True
    status = await client.get_status()
    assert status.volume == 24
    assert fake_player.connections == 1
    await client.close()


async def test_broadcast_frames_are_skipped(fake_player) -> None:
    """Other clients' traffic can arrive before our reply."""
    fake_player.broadcast_before_reply = "VOL:50;"
    fake_player.playing = True
    client = LPClient("127.0.0.1", fake_player.port)
    seen: list[str] = []
    client.add_frame_listener(seen.append)
    assert await client.get_playing() is True
    assert seen == ["VOL:50"]
    await client.close()


async def test_controls(fake_player) -> None:
    client = LPClient("127.0.0.1", fake_player.port)
    await client.set_volume(40)
    await client.set_mute(True)
    await client.set_source("BT")
    await client.play_pause()
    await client.get_playing()  # round-trip so the writes have landed
    assert fake_player.received[:4] == ["VOL:40", "MUT:1", "SRC:BT", "POP"]
    with pytest.raises(ValueError):
        await client.set_volume(101)
    with pytest.raises(ValueError):
        await client.set_source("TAPE")
    await client.close()


async def test_timeout_then_backoff_then_recovery(fake_player, monkeypatch) -> None:
    monkeypatch.setattr(client_mod, "QUERY_TIMEOUT", 0.2)
    client = LPClient("127.0.0.1", fake_player.port)
    fake_player.respond = False
    with pytest.raises(LPConnectionError):
        await client.query("PLA;", timeout=0.2)
    # Immediately after a failure the client backs off rather than hammering.
    with pytest.raises(LPConnectionError, match="deferred"):
        await client.get_playing()
    fake_player.respond = True
    client._retry_at = 0  # skip the wait
    assert await client.get_playing() is False
    assert client.stats["timeouts"] == 1
    await client.close()


async def test_reconnects_after_server_drops(fake_player) -> None:
    client = LPClient("127.0.0.1", fake_player.port)
    await client.get_playing()
    fake_player.drop_clients()
    # The next query may fail on the dead socket; after backoff it reconnects.
    try:
        await client.get_playing()
    except LPConnectionError:
        client._retry_at = 0
        await client.get_playing()
    assert fake_player.connections == 2
    await client.close()


async def test_unreachable_host(socket_enabled: None) -> None:
    client = LPClient("127.0.0.1", 1)
    with pytest.raises(LPConnectionError):
        await client.get_playing()
    await client.close()
    with pytest.raises(LPConnectionError, match="closed"):
        await client.get_playing()


async def _airplay_server(monkeypatch, body: bytes) -> asyncio.Server:
    """Serve ``body`` as the AirPlay /info reply on a local port."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.0 200 OK\r\n\r\n" + body)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    monkeypatch.setattr(client_mod, "AIRPLAY_PORT", server.sockets[0].getsockname()[1])
    return server


async def test_airplay_session_flag(socket_enabled: None, monkeypatch) -> None:
    body = plistlib.dumps({"statusFlags": 0x804}, fmt=plistlib.FMT_BINARY)
    server = await _airplay_server(monkeypatch, body)
    async with server:
        assert await async_airplay_active("127.0.0.1") is True


async def test_oversized_airplay_reply_is_refused(socket_enabled: None, monkeypatch) -> None:
    server = await _airplay_server(monkeypatch, b"x" * (client_mod.MAX_AIRPLAY_REPLY + 1))
    async with server:
        with pytest.raises(LPProtocolError, match="Oversized"):
            await async_airplay_active("127.0.0.1")


def test_discover_ignores_replies_from_other_hosts(socket_enabled: None) -> None:
    """Only the player's own address may answer the identity query."""
    player = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    player.bind(("127.0.0.1", 0))
    impostor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    impostor.bind(("127.0.0.2", 0))

    def respond() -> None:
        _, requester = player.recvfrom(1024)
        impostor.sendto(b"HTTP/1.1 200 OK\r\nUSN: ffffffffffff\r\n", requester)
        time.sleep(0.05)
        player.sendto(b"HTTP/1.1 200 OK\r\nUSN: a0b1c2d3e4f5\r\nDeviceName: Lounge\r\n", requester)

    thread = threading.Thread(target=respond)
    thread.start()
    try:
        identity = discover("127.0.0.1", timeout=2, port=player.getsockname()[1])
    finally:
        thread.join()
        player.close()
        impostor.close()
    assert device_id(identity) == "a0b1c2d3e4f5"
    assert identity["devicename"] == "Lounge"
