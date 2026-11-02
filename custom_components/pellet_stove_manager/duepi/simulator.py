"""In-process simulated stove (development, demos and tests without hardware).

Same `request(command)` interface as DuepiTransport; simple cycle
off -> ignition -> heating -> cooling -> off.
"""

from __future__ import annotations

import time

PHASES = (
    (0x00000020, 20),  # off
    (0x01010022, 12),  # ignition
    (0x02010023, 90),  # heating
    (0x08010029, 15),  # cooling
)


class SimulatedStove:
    strays = 0

    def __init__(self, speed: float = 1, hours_total: int = 1735, hours_service: int = 12, error: int = 0) -> None:
        self.speed = speed
        self.start = time.monotonic()
        self.hours_total = hours_total
        self.hours_service = hours_service
        self.error = error
        self.power = 3
        self.setpoint = 20
        self.writes: list[str] = []

    def _phase(self) -> int:
        cycle = sum(m for _, m in PHASES)
        t = ((time.monotonic() - self.start) / 60 * self.speed) % cycle
        for status, minutes in PHASES:
            if t < minutes:
                return status
            t -= minutes
        return PHASES[0][0]

    def reply(self, command: str) -> str:
        status = 0x00000020 if self.error else self._phase()
        heating, ignition, cooling = status == 0x02010023, status == 0x01010022, status == 0x08010029

        def pad(v: int) -> str:
            return f"{v:04X}0020"

        if command.startswith("F") or command == "D6000":
            self.writes.append(command)
            return "00000020"
        return {
            "D9000": f"{status:08X}",
            "DA000": pad(self.error),
            "D3000": pad(self.power if heating or ignition else 1),
            "D0000": pad(95 + self.power * 15 if heating else 70 if cooling else 45 if ignition else 20),
            "DF000": pad(38 if heating else 33),
            "D4000": pad(10 + self.power * 4 if heating else 0),
            "EF000": pad(140 if heating or ignition or cooling else 0),
            "D1000": "03E7003F",  # no ambient probe
            "C6000": pad(self.setpoint),
            "C0000": "01000021",
            "ED000": f"{self.hours_total:06X}40",
            "EE000": f"{self.hours_service:06X}33",
        }.get(command, "00000020")

    async def request(self, command: str) -> str:
        return self.reply(command)

    async def close(self) -> None:
        return None
