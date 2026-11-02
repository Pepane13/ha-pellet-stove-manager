"""Duepi EVO command set and reply decoders.

Sources: community documentation (aceindy/Duepi_EVO, P. Bornat "InterstoveRemoteCmd",
motionbug/testcodestove SPEC.md) and captures of the owner's own stove. Ported from
com.pepane13.pelletstove 0.9.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

READ: dict[str, str] = {
    "status": "D9000",
    "power_level": "D3000",
    "ambient": "D1000",
    "flue_gas": "D0000",
    "auger": "D4000",
    "exhaust": "EF000",
    "error": "DA000",
    "setpoint": "C6000",
    "board": "DF000",
    "hours_total": "ED000",
    "hours_service": "EE000",
    "pressure_switch": "C0000",
}

ACK = 0x00000020


def write_power(level: int) -> str:
    """Power 1..5, 0 = shutdown."""
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 5:
        raise ValueError(f"Invalid power level {level}")
    return f"F00{level}0"


def write_setpoint(celsius: float) -> str:
    n = round(float(celsius))
    if not 5 <= n <= 35:
        raise ValueError(f"Invalid setpoint {celsius}")
    return f"F2{n:02X}0"


def write_reset_alarm() -> str:
    return "D6000"


WRITE: dict[str, Callable[..., str]] = {
    "power": write_power,
    "setpoint": write_setpoint,
    "reset_alarm": write_reset_alarm,
}


@dataclass(frozen=True)
class StoveError:
    code: int
    id: str
    en: str
    fr: str
    severity: int


ERRORS: dict[int, tuple[str, str, str, int]] = {
    0: ("none", "No error", "Aucune erreur", 0),
    1: ("ignition_failure", "Ignition failure", "Échec d'allumage", 2),
    2: ("suction_failure", "Defective suction", "Aspiration défectueuse", 2),
    3: ("air_intake", "Insufficient air intake", "Entrée d'air insuffisante", 2),
    4: ("water_temperature", "Water temperature", "Température d'eau", 2),
    5: ("out_of_pellets", "Out of pellets", "Manque de pellets", 1),
    6: ("pressure_switch", "Defective pressure switch", "Pressostat défectueux", 3),
    8: ("power_failure", "Power failure", "Coupure de courant", 1),
    9: ("exhaust_motor", "Exhaust motor failure", "Moteur d'extraction", 3),
    10: ("board_overvoltage", "Board overvoltage", "Surtension carte", 3),
    11: ("date_expired", "Date expired", "Date expirée", 1),
    13: ("suction_sensor", "Suction regulation sensor error", "Sonde de régulation d'aspiration", 3),
    14: ("overheating", "Overheating", "Surchauffe", 3),
}

STATUSES = ("off", "ignition", "heating", "cleaning", "eco", "cooling", "unknown")


def _hex4(p: str) -> int:
    return int(p[:4], 16)


def _hex6(p: str) -> int:
    return int(p[:6], 16)


def decode_status(payload: str) -> str:
    v = int(payload, 16)
    phase = (v >> 24) & 0xFF
    if phase & 0x01:
        return "ignition"
    if phase & 0x02:
        return "heating"
    if phase & 0x04:
        return "cleaning"
    if phase & 0x10:
        return "eco"
    if phase & 0x08:
        return "cooling"
    if v == ACK:
        return "off"
    return "unknown"


def decode_error(payload: str) -> StoveError:
    code = _hex4(payload)
    if code in ERRORS:
        ident, en, fr, sev = ERRORS[code]
        return StoveError(code, ident, en, fr, sev)
    return StoveError(code, f"unknown_{code}", f"Unknown error {code}", f"Erreur inconnue {code}", 2)


def decode_ambient(payload: str) -> float | None:
    """99.9 °C means 'no probe connected' (stove regulated by an external thermostat)."""
    t = _hex4(payload) / 10
    return None if t >= 99 else t


def decode_setpoint(payload: str) -> int | None:
    v = _hex4(payload)
    return v if 5 <= v <= 35 else None


def decode_pressure_switch(payload: str) -> str:
    v = _hex4(payload)
    if v == 0x0100:
        return "normal"
    if v == 0x0300:
        return "pressure"
    return f"0x{v:x}"


DECODERS: dict[str, Callable[[str], Any]] = {
    "status": decode_status,
    "power_level": _hex4,
    "ambient": decode_ambient,
    "flue_gas": _hex4,
    "auger": _hex4,
    "exhaust": lambda p: _hex4(p) * 10,  # rpm
    "error": decode_error,
    "setpoint": decode_setpoint,
    "board": _hex4,
    "hours_total": _hex6,
    "hours_service": _hex6,
    "pressure_switch": decode_pressure_switch,
}


def is_ack(payload: str) -> bool:
    try:
        return (int(payload, 16) & ACK) == ACK
    except (TypeError, ValueError):
        return False
