"""Async client for an Arylic LP-series player's LAN interfaces.

TCP 2018 carries Arylic's plain-text UART API (``XXX;`` -> ``XXX:<value>;``),
relayed to the MCU by the firmware's ``tcptunnelling`` daemon. It has no
authentication and accepts several clients at once; replies may be broadcast
to every connected client, so responses are matched by their prefix.

This module has no Home Assistant imports so it can be exercised directly
against a device or a fake server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import plistlib
import socket
import time
from typing import Any

from .const import AIRPLAY_PORT, CONTROL_PORT, DISCOVERY_PORT

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 3.0
QUERY_TIMEOUT = 1.5
MAX_FRAME = 8192
# The LP10's AirPlay /info reply is ~770 bytes; anything far bigger isn't it.
MAX_AIRPLAY_REPLY = 65536
MAX_SOURCE_LEN = 32
BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 30.0

# Sources accepted by ``SRC:<name>;`` on the player.
SOURCES: tuple[str, ...] = ("NET", "BT", "LINE-IN", "USBPLAY")


class LPError(Exception):
    """Base error for player communication."""


class LPConnectionError(LPError):
    """The player could not be reached or did not answer."""


class LPProtocolError(LPError):
    """The player answered with something unexpected."""


TONE_MIN = -10
TONE_MAX = 10


@dataclass(frozen=True, slots=True)
class LPStatus:
    """Parsed ``STA;`` reply, plus settings ``STA`` doesn't carry (mid, virtual bass)."""

    source: str
    muted: bool
    volume: int
    treble: int | None
    bass: int | None
    playing: bool
    led: bool | None = None
    mid: int | None = None
    virtual_bass: bool | None = None


def parse_status(reply: str) -> LPStatus:
    """Parse ``STA:NET,0,24,3,0,3,0,0,1,0``.

    Field order: source, mute, volume, treble, bass, net, internet, playing,
    led, upgrading. Values are checked like single-setting frames; an
    out-of-range tone reads as unknown.
    """
    if not reply.startswith("STA:"):
        raise LPProtocolError(f"Not a status reply: {reply!r}")
    fields = reply[4:].rstrip(";").split(",")
    if len(fields) < 8:
        raise LPProtocolError(f"Short status reply: {reply!r}")
    try:
        source = _source(fields[0])
        volume = _volume(fields[2])
    except ValueError as err:
        raise LPProtocolError(f"Invalid status reply: {reply[:64]!r}") from err
    return LPStatus(
        source=source,
        muted=fields[1] == "1",
        volume=volume,
        treble=_optional(_tone, fields[3]),
        bass=_optional(_tone, fields[4]),
        playing=fields[7] == "1",
        led=fields[8] == "1" if len(fields) > 8 else None,
    )


def _optional(parser: Callable[[str], Any], value: str) -> Any:
    try:
        return parser(value)
    except ValueError:
        return None


def _source(value: str) -> str:
    if not 0 < len(value) <= MAX_SOURCE_LEN or not value.isprintable():
        raise ValueError(value)
    return value


def _volume(value: str) -> int:
    level = int(value)
    if not 0 <= level <= 100:
        raise ValueError(value)
    return level


def _tone(value: str) -> int:
    level = int(value)
    if not TONE_MIN <= level <= TONE_MAX:
        raise ValueError(value)
    return level


def _flag(value: str) -> bool:
    if value not in ("0", "1"):
        raise ValueError(value)
    return value == "1"


# Single-setting frames (``VOL:30``) the player sends as replies, which are
# broadcast to every client: command -> (LPStatus field, parser).
STATUS_FRAMES: dict[str, tuple[str, Callable[[str], Any]]] = {
    "VOL": ("volume", _volume),
    "MUT": ("muted", _flag),
    "SRC": ("source", _source),
    "TRE": ("treble", _tone),
    "BAS": ("bass", _tone),
    "MID": ("mid", _tone),
    "LED": ("led", _flag),
    "VBS": ("virtual_bass", _flag),
}


def parse_setting(frame: str) -> tuple[str, Any] | None:
    """``TRE:3`` -> ``("treble", 3)``; None for other or malformed frames."""
    command, sep, value = frame.rstrip(";").partition(":")
    if not sep or command not in STATUS_FRAMES:
        return None
    field, parser = STATUS_FRAMES[command]
    try:
        return field, parser(value)
    except ValueError:
        return None


def parse_play_state(reply: str) -> bool:
    """Parse ``PLA:1`` (playing) / ``PLA:0`` (paused or stopped)."""
    if reply in ("PLA:1", "PLA:1;"):
        return True
    if reply in ("PLA:0", "PLA:0;"):
        return False
    raise LPProtocolError(f"Unexpected play state reply: {reply!r}")


class LPClient:
    """One persistent, serialized connection to a player's TCP 2018 port.

    Connects lazily, reconnects on demand with exponential backoff, and never
    replays a write after a failure.
    """

    def __init__(self, host: str, port: int = CONTROL_PORT) -> None:
        self.host = host
        self.port = port
        self._lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: tuple[str, asyncio.Future[str]] | None = None
        self._failures = 0
        self._retry_at = 0.0
        self._closed = False
        self._frame_listeners: list[Callable[[str], None]] = []
        self.stats: dict[str, int] = {
            "connects": 0,
            "disconnects": 0,
            "queries": 0,
            "writes": 0,
            "timeouts": 0,
            "unsolicited_frames": 0,
        }

    @property
    def connected(self) -> bool:
        """Whether a connection is currently open."""
        return self._writer is not None and not self._writer.is_closing()

    def add_frame_listener(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """Receive every frame not consumed as a query reply."""
        self._frame_listeners.append(listener)
        return lambda: self._frame_listeners.remove(listener)

    async def _ensure_connected(self) -> asyncio.StreamWriter:
        if self._closed:
            raise LPConnectionError("Client is closed")
        if self._writer is not None and not self._writer.is_closing():
            return self._writer
        wait = self._retry_at - time.monotonic()
        if wait > 0:
            raise LPConnectionError(f"Reconnect to {self.host} deferred {wait:.1f}s")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError) as err:
            self._record_failure()
            raise LPConnectionError(f"Cannot connect to {self.host}:{self.port}") from err
        self._writer = writer
        self.stats["connects"] += 1
        self._reader_task = asyncio.create_task(
            self._read_loop(reader, writer), name=f"arylic-lp-reader-{self.host}"
        )
        return writer

    def _record_failure(self) -> None:
        self._failures = min(self._failures + 1, 10)
        delay = min(BACKOFF_MAX, BACKOFF_INITIAL * 2 ** (self._failures - 1))
        self._retry_at = time.monotonic() + delay

    async def _read_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                while (end := buffer.find(b";")) != -1:
                    frame = bytes(buffer[:end]).decode("ascii", "replace")
                    del buffer[: end + 1]
                    self._dispatch(frame.strip("\x00\r\n "))
                if len(buffer) > MAX_FRAME:
                    _LOGGER.debug("Oversized frame from %s; resetting", self.host)
                    break
        except (OSError, asyncio.IncompleteReadError) as err:
            _LOGGER.debug("Read from %s failed: %s", self.host, err)
        finally:
            self._drop(writer)

    def _dispatch(self, frame: str) -> None:
        if not frame:
            return
        if self._pending is not None:
            prefix, future = self._pending
            if frame.startswith(prefix) and not future.done():
                future.set_result(frame)
                return
        self.stats["unsolicited_frames"] += 1
        for listener in list(self._frame_listeners):
            try:
                listener(frame)
            except Exception:  # noqa: BLE001 - a listener must not kill the reader
                _LOGGER.exception("Frame listener failed")

    def _drop(self, writer: asyncio.StreamWriter) -> None:
        """Forget a connection (called from the reader or on error)."""
        if self._writer is writer:
            self._writer = None
            self.stats["disconnects"] += 1
        if not writer.is_closing():
            writer.close()
        if self._pending is not None and not self._pending[1].done():
            self._pending[1].set_exception(LPConnectionError(f"Connection to {self.host} closed"))

    async def query(self, command: str, timeout: float = QUERY_TIMEOUT) -> str:
        """Send a query like ``PLA;`` and return its reply, e.g. ``PLA:1``."""
        if not command.endswith(";") or command.count(";") != 1:
            raise ValueError("Expected one ';'-terminated command")
        prefix = command[:-1] + ":"
        async with self._lock:
            writer = await self._ensure_connected()
            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self._pending = (prefix, future)
            try:
                writer.write(command.encode("ascii"))
                await writer.drain()
                self.stats["queries"] += 1
                reply = await asyncio.wait_for(future, timeout)
            except TimeoutError as err:
                # A late reply must not satisfy the next query: start afresh.
                self.stats["timeouts"] += 1
                self._drop(writer)
                self._record_failure()
                raise LPConnectionError(f"{self.host} did not answer {command}") from err
            except OSError as err:
                self._drop(writer)
                self._record_failure()
                raise LPConnectionError(f"Query to {self.host} failed") from err
            except LPConnectionError:
                # The reader saw the connection close while we waited.
                self._record_failure()
                raise
            finally:
                self._pending = None
            self._failures = 0
            self._retry_at = 0.0
            return reply

    async def write(self, command: str) -> None:
        """Send a control command once. Success means sent, not applied."""
        if not command.endswith(";") or command.count(";") != 1:
            raise ValueError("Expected one ';'-terminated command")
        async with self._lock:
            writer = await self._ensure_connected()
            try:
                writer.write(command.encode("ascii"))
                await writer.drain()
            except OSError as err:
                self._drop(writer)
                self._record_failure()
                raise LPConnectionError(f"Write to {self.host} failed") from err
            self.stats["writes"] += 1

    async def close(self) -> None:
        """Close for good; later calls raise."""
        self._closed = True
        if self._writer is not None:
            self._drop(self._writer)
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError, Exception:  # noqa: BLE001
                pass

    # Queries

    async def get_playing(self) -> bool:
        """True while the player is playing from any source."""
        return parse_play_state(await self.query("PLA;"))

    async def get_status(self) -> LPStatus:
        """Source, volume, mute and tone."""
        return parse_status(await self.query("STA;"))

    # Controls (commands verified on AR241 firmware)

    async def set_volume(self, volume: int) -> None:
        if not 0 <= volume <= 100:
            raise ValueError("Volume must be 0-100")
        await self.write(f"VOL:{volume};")

    async def set_mute(self, muted: bool) -> None:
        await self.write(f"MUT:{int(muted)};")

    async def set_source(self, source: str) -> None:
        if source not in SOURCES:
            raise ValueError(f"Unknown source {source!r}")
        await self.write(f"SRC:{source};")

    async def get_mid(self) -> int:
        """Mid tone in dB (-10..10); ``STA`` doesn't include it."""
        reply = await self.query("MID;")
        setting = parse_setting(reply)
        if setting is None:
            raise LPProtocolError(f"Unexpected mid reply: {reply!r}")
        return setting[1]

    async def get_virtual_bass(self) -> bool:
        """Virtual bass on/off; ``STA`` doesn't include it."""
        reply = await self.query("VBS;")
        setting = parse_setting(reply)
        if setting is None:
            raise LPProtocolError(f"Unexpected virtual bass reply: {reply!r}")
        return setting[1]

    async def set_tone(self, command: str, level: int) -> None:
        """Set ``BAS``, ``MID`` or ``TRE`` to -10..10 dB."""
        if command not in ("BAS", "MID", "TRE"):
            raise ValueError(f"Unknown tone {command!r}")
        if not TONE_MIN <= level <= TONE_MAX:
            raise ValueError(f"Tone must be {TONE_MIN}..{TONE_MAX}")
        await self.write(f"{command}:{level};")

    async def set_led(self, on: bool) -> None:
        await self.write(f"LED:{int(on)};")

    async def set_virtual_bass(self, on: bool) -> None:
        await self.write(f"VBS:{int(on)};")

    async def play_pause(self) -> None:
        await self.write("POP;")

    async def next_track(self) -> None:
        await self.write("NXT;")

    async def previous_track(self) -> None:
        await self.write("PRE;")


async def async_airplay_active(host: str, timeout: float = 2.0) -> bool:
    """True while an AirPlay receiver session is active (statusFlags bit 0x800)."""
    request = f"GET /info HTTP/1.0\r\nHost: {host}\r\n\r\n".encode("ascii")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, AIRPLAY_PORT), timeout
        )
    except (OSError, TimeoutError) as err:
        raise LPConnectionError(f"AirPlay endpoint on {host} unreachable") from err
    raw = bytearray()
    try:
        writer.write(request)
        await writer.drain()
        async with asyncio.timeout(timeout):
            while chunk := await reader.read(4096):
                raw += chunk
                if len(raw) > MAX_AIRPLAY_REPLY:
                    raise LPProtocolError(f"Oversized AirPlay info from {host}")
    except (OSError, TimeoutError) as err:
        raise LPConnectionError(f"AirPlay endpoint on {host} did not answer") from err
    finally:
        writer.close()
    _, _, body = bytes(raw).partition(b"\r\n\r\n")
    try:
        info = plistlib.loads(body)
        return bool(int(info["statusFlags"]) & 0x800)
    except (plistlib.InvalidFileException, KeyError, ValueError, TypeError) as err:
        raise LPProtocolError(f"Unexpected AirPlay info from {host}") from err


def discover(host: str, timeout: float = 1.0, port: int = DISCOVERY_PORT) -> dict[str, Any]:
    """Blocking unicast LSSDP identity query on UDP 1800.

    Returns lower-cased header fields (``usn``, ``devicename``, ``fwversion``,
    ``cast_model``, ``netmode``...). Only a reply from the player's own
    address counts, so another host on the LAN can't answer in its place.
    Run in an executor.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            address = socket.gethostbyname(host)
            sock.bind(("0.0.0.0", 0))
            local_port = sock.getsockname()[1]
            request = (
                f"M-SEARCH * HTTP/1.1\r\nHOST: 0.0.0.0:{local_port}\r\n\r\nPROTOCOL: Version 1.0"
            ).encode("ascii")
            sock.sendto(request, (address, port))
            deadline = time.monotonic() + timeout
            while True:
                if (remaining := deadline - time.monotonic()) <= 0:
                    raise TimeoutError
                sock.settimeout(remaining)
                payload, (sender, _) = sock.recvfrom(8192)
                if sender == address:
                    break
                _LOGGER.debug("Ignoring discovery reply from %s (asked %s)", sender, address)
        except OSError as err:
            raise LPConnectionError(f"No discovery reply from {host}") from err
    result: dict[str, Any] = {}
    for line in payload.decode("utf-8", "replace").replace("\r", "").split("\n"):
        key, sep, value = line.partition(":")
        if sep:
            result[key.strip().lower()] = value.strip()[:128]
    return result


def device_id(identity: dict[str, Any]) -> str | None:
    """Stable device id (MAC-like USN) from a discovery reply, if present."""
    usn = str(identity.get("usn", "")).lower().replace(":", "")
    if len(usn) == 12 and all(c in "0123456789abcdef" for c in usn):
        return usn
    return None
