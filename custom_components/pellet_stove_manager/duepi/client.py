"""High-level Duepi client: snapshots and (opt-in) write commands."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from .protocol import DECODERS, READ, WRITE

# Order matters: status first; if it fails the snapshot is rejected.
SNAPSHOT = (
    "status",
    "error",
    "power_level",
    "flue_gas",
    "board",
    "auger",
    "exhaust",
    "ambient",
    "setpoint",
    "pressure_switch",
    "hours_total",
    "hours_service",
)


class _Transport(Protocol):
    strays: int

    async def request(self, command: str) -> str: ...


class DesyncError(RuntimeError):
    """Replies arrived out of order: the snapshot is discarded."""


class DuepiClient:
    def __init__(self, transport: _Transport, settle: float = 0.4) -> None:
        self.transport = transport
        self.settle = settle

    async def read(self, name: str) -> Any:
        payload = await self.transport.request(READ[name])
        return DECODERS[name](payload)

    async def write(self, name: str, *args: Any) -> str:
        """Send a write command. Callers must check the « allow commands » option."""
        if name not in WRITE:
            raise ValueError(f"Unknown command {name}")
        return await self.transport.request(WRITE[name](*args))

    async def snapshot(self, names: tuple[str, ...] = SNAPSHOT) -> dict[str, Any]:
        """Read a full state snapshot; optional values that fail are listed in `missing`.

        Duepi replies carry no command identifier. If the relay delivers one extra frame,
        every following reply would be matched to the wrong command; any stray frame during
        the snapshot (or just after it) discards the whole snapshot.
        """
        tracked = isinstance(getattr(self.transport, "strays", None), int)
        before = self.transport.strays if tracked else 0
        state: dict[str, Any] = {"at": time.time(), "missing": []}
        for name in names:
            try:
                state[name] = await self.read(name)
            except Exception:  # noqa: BLE001
                if name == "status":
                    raise
                state["missing"].append(name)
        if tracked:
            if self.settle:
                await asyncio.sleep(self.settle)
            if self.transport.strays != before:
                raise DesyncError("Replies out of order: snapshot discarded")
        return state
