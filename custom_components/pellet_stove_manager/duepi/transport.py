"""Asyncio TCP transport to a Duepi EVO stove.

- mode "cloud": connects to the Duepi relay (1.duepiwebserver1.com:3000), logs in with
  "master:<code>#" and exchanges AES-encrypted frames. One session is kept open and reused.
- mode "local": plain frames to a serial bridge on the LAN (ESP-Link / ser2net).

Commands are strictly serialised: the stove answers one request at a time and a late reply
must never be matched to another command. A reply timeout invalidates the session.
Consecutive connection failures are spaced by an exponential back-off (5 s to 10 min) to
stay gentle with the vendor relay. The device code is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

from . import codec

_LOGGER = logging.getLogger(__name__)

CLOUD_HOST = "1.duepiwebserver1.com"
CLOUD_PORT = 3000
LOCAL_PORT = 23


@dataclass
class TransportOptions:
    reply_timeout: float = 4.0
    connect_timeout: float = 10.0
    login_delay: float = 0.5
    inter_command_delay: float = 0.15
    idle_close: float = 300.0
    backoff_min: float = 5.0
    backoff_max: float = 600.0


class BackoffError(ConnectionError):
    """Reconnection is delayed by the back-off."""


class DuepiTransport:
    def __init__(
        self,
        mode: str,
        host: str | None = None,
        port: int | None = None,
        code: str | None = None,
        options: TransportOptions | None = None,
        on_connection_change: Callable[[bool, str | None], None] | None = None,
    ) -> None:
        self.mode = "local" if mode == "local" else "cloud"
        self.host = host or (CLOUD_HOST if self.mode == "cloud" else None)
        self.port = int(port) if port else (CLOUD_PORT if self.mode == "cloud" else LOCAL_PORT)
        self.code = str(code).strip().rstrip("#") if code else None
        self.opts = options or TransportOptions()
        self._on_change = on_connection_change
        if not self.host:
            raise ValueError("Host is required")
        self.key: bytes | None = None
        if self.mode == "cloud":
            if not self.code:
                raise ValueError("Device code is required in cloud mode")
            self.key = codec.derive_key(self.code)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._parser: codec.FrameParser | None = None
        self._pending: tuple[str, asyncio.Future] | None = None
        self._lock = asyncio.Lock()
        self._idle_handle: asyncio.TimerHandle | None = None
        self.failures = 0
        self.next_attempt = 0.0
        self.closed = False
        #: frames received while no command was waiting (desync detector)
        self.strays = 0

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def request(self, command: str) -> str:
        """Send one command and return its 8-character reply payload."""
        async with self._lock:
            if self.closed:
                raise ConnectionError("Transport closed")
            await self._ensure_connected()
            frame = (
                codec.encrypt_frame(command, self.key)
                if self.mode == "cloud"
                else codec.plain_frame(command)
            )
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending = (command, fut)
            assert self._writer is not None
            self._writer.write(frame)
            try:
                await self._writer.drain()
                reply = await asyncio.wait_for(fut, self.opts.reply_timeout)
            except asyncio.TimeoutError as err:
                self._pending = None
                await self._drop(f"No reply to {command}")
                raise TimeoutError(f"Timeout waiting for reply to {command}") from err
            except (OSError, ConnectionError):
                self._pending = None
                await self._drop("write failed")
                raise
            self._pending = None
            self._arm_idle()
            if self.opts.inter_command_delay:
                await asyncio.sleep(self.opts.inter_command_delay)
            return reply

    async def _ensure_connected(self) -> None:
        if self.connected:
            return
        wait = self.next_attempt - time.monotonic()
        if wait > 0:
            raise BackoffError(f"Reconnection delayed ({int(wait) + 1} s)")
        try:
            await self._connect()
        except (OSError, asyncio.TimeoutError, ConnectionError) as err:
            self.failures += 1
            delay = min(self.opts.backoff_max, self.opts.backoff_min * 2 ** (self.failures - 1))
            self.next_attempt = time.monotonic() + delay
            _LOGGER.debug("Connection to %s:%s failed (%s), retry in %.0f s", self.host, self.port, err, delay)
            raise ConnectionError(str(err)) from err
        self.failures = 0
        self.next_attempt = 0.0

    async def _connect(self) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.opts.connect_timeout
        )
        self._reader, self._writer = reader, writer
        self._parser = codec.FrameParser(self.key)
        if self.mode == "cloud":
            writer.write(f"master:{self.code}#".encode("ascii"))
            await writer.drain()
        self._read_task = asyncio.get_running_loop().create_task(self._read_loop(reader, writer))
        await asyncio.sleep(self.opts.login_delay)
        if not self.connected:
            raise ConnectionError("Connection closed during login")
        _LOGGER.debug("Connected to %s:%s (%s)", self.host, self.port, self.mode)
        if self._on_change:
            self._on_change(True, None)

    async def _read_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                if writer is not self._writer or self._parser is None:
                    return  # superseded socket
                for frame in self._parser.push(chunk):
                    pending = self._pending
                    if pending is None or pending[1].done():
                        self.strays += 1
                        _LOGGER.debug("Unexpected frame (no command waiting)")
                        continue
                    if not frame.valid:
                        _LOGGER.debug("Ignored invalid frame for %s", pending[0])
                        continue
                    pending[1].set_result(frame.payload)
        except (OSError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            return
        if writer is self._writer:
            await self._drop("Connection closed by peer", from_reader=True)

    async def _drop(self, reason: str, from_reader: bool = False) -> None:
        was_connected = self._writer is not None
        writer, task = self._writer, self._read_task
        self._writer = self._reader = None
        self._parser = None
        self._read_task = None
        if self._idle_handle:
            self._idle_handle.cancel()
            self._idle_handle = None
        if self._pending and not self._pending[1].done():
            self._pending[1].set_exception(ConnectionError(reason))
        self._pending = None
        if task and not from_reader:
            task.cancel()
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass
        if was_connected and self._on_change:
            self._on_change(False, reason)

    def _arm_idle(self) -> None:
        if self._idle_handle:
            self._idle_handle.cancel()
        if not self.opts.idle_close:
            return
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(
            self.opts.idle_close, lambda: loop.create_task(self._drop("Idle session closed"))
        )

    async def close(self) -> None:
        self.closed = True
        await self._drop("Transport closed")
