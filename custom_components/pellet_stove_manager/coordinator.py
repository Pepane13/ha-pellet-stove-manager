"""Stove coordinator: reads the stove (Duepi relay, local bridge, simulator) or mirrors
the Homey app through its homeyHASS entities, and raises the same events as the Homey
device's Flow triggers."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CODE,
    CONF_HOST,
    CONF_MIRROR_PREFIX,
    CONF_MODE,
    CONF_POLL,
    CONF_PORT,
    DEFAULT_MIRROR_PREFIX,
    DEFAULT_POLL,
    DOMAIN,
    EVENT,
    MIN_POLL,
    MODE_CLOUD,
    MODE_LOCAL,
    MODE_MIRROR,
    MODE_SIMULATOR,
    RECHECK_DELAY,
    UNAVAILABLE_AFTER,
)
from .duepi.client import DesyncError, DuepiClient
from .duepi.protocol import ERRORS, StoveError
from .duepi.simulator import SimulatedStove
from .duepi.transport import DuepiTransport

_LOGGER = logging.getLogger(__name__)

NO_ERROR = StoveError(0, "none", "No error", "Aucune erreur", 0)

# homeyHASS entity suffixes of the Homey stove device (mirror mode)
MIRROR = {
    "status": "select.{p}_stove_status",
    "power_level": "sensor.{p}_stove_power_level",
    "flue_gas": "sensor.{p}_flue_gas_temperature",
    "board": "sensor.{p}_board_temperature",
    "auger": "sensor.{p}_stove_auger_speed",
    "exhaust": "sensor.{p}_stove_exhaust_speed",
    "error": "sensor.{p}_stove_error",
    "alarm": "binary_sensor.{p}_generic",
    "hours_total": "sensor.{p}_stove_hours_total",
    "hours_service": "sensor.{p}_stove_hours_service",
}


def _error_from_text(text: str | None, alarm: bool) -> StoveError:
    """Map the Homey error message back to a Duepi error (mirror mode)."""
    if not alarm or not text or text in ("—", "-"):
        return NO_ERROR
    for code, (ident, en, fr, sev) in ERRORS.items():
        if text.strip().lower() in (en.lower(), fr.lower(), ident):
            return StoveError(code, ident, en, fr, sev)
    return StoveError(-1, "unknown", text, text, 2)


class StoveCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        opts = {**entry.data, **entry.options}
        self.entry = entry
        self.mode: str = opts.get(CONF_MODE, MODE_MIRROR)
        poll = max(MIN_POLL, int(opts.get(CONF_POLL, DEFAULT_POLL)))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll),
            always_update=False,
        )
        self.prefix: str = opts.get(CONF_MIRROR_PREFIX, DEFAULT_MIRROR_PREFIX)
        self.transport: DuepiTransport | SimulatedStove | None = None
        self.client: DuepiClient | None = None
        if self.mode in (MODE_CLOUD, MODE_LOCAL):
            self.transport = DuepiTransport(
                self.mode,
                host=opts.get(CONF_HOST) or None,
                port=opts.get(CONF_PORT) or None,
                code=opts.get(CONF_CODE),
            )
        elif self.mode == MODE_SIMULATOR:
            self.transport = SimulatedStove(speed=10)
        if self.transport is not None:
            self.client = DuepiClient(self.transport)
        self.failures = 0
        self.connected = self.mode == MODE_MIRROR
        self._last_status: str | None = None
        self._last_error: StoveError | None = None
        self._candidate_error: StoveError | None = None
        self._unsub: list = []

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        if self.mode == MODE_MIRROR:
            ids = [t.format(p=self.prefix) for t in MIRROR.values()]
            self._unsub.append(async_track_state_change_event(self.hass, ids, self._on_mirror_change))
        await self.async_config_entry_first_refresh()

    async def async_stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self.transport is not None:
            await self.transport.close()

    @callback
    def _on_mirror_change(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    # ------------------------------------------------------------------ reading
    async def _async_update_data(self) -> dict[str, Any]:
        if self.mode == MODE_MIRROR:
            return self._apply(self._mirror_snapshot())
        assert self.client is not None
        try:
            state = await self.client.snapshot()
        except DesyncError as err:
            _LOGGER.debug("%s", err)
            self._recheck()
            if self.data:
                return self.data
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            self.failures += 1
            _LOGGER.debug("Snapshot failed (%s): %s", self.failures, err)
            if self.failures == UNAVAILABLE_AFTER:
                self.connected = False
                self._fire("stove_connection_lost", {})
            if self.data and self.failures < UNAVAILABLE_AFTER:
                return self.data
            raise UpdateFailed(str(err)) from err
        if self.failures >= UNAVAILABLE_AFTER:
            _LOGGER.info("Stove connection restored")
        self.failures = 0
        self.connected = True
        return self._apply(state)

    def _mirror_snapshot(self) -> dict[str, Any]:
        def st(key: str) -> str | None:
            s = self.hass.states.get(MIRROR[key].format(p=self.prefix))
            if s is None or s.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                return None
            return s.state

        def num(key: str) -> float | None:
            try:
                return float(st(key))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        status = st("status")
        if status is None:
            self.connected = False
            raise UpdateFailed(f"Mirror entity {MIRROR['status'].format(p=self.prefix)} unavailable")
        self.connected = True
        alarm = st("alarm") == "on"
        state: dict[str, Any] = {
            "status": "unknown" if status == "alarm" else status,
            "error": _error_from_text(st("error"), alarm or status == "alarm"),
            "missing": [],
            "source": "mirror",
        }
        for key in ("power_level", "flue_gas", "board", "auger", "exhaust", "hours_total", "hours_service"):
            value = num(key)
            state[key] = None if value is None else (int(value) if key.startswith(("power", "hours")) else value)
        state["ambient"] = None
        state["setpoint"] = None
        state["pressure_switch"] = None
        return state

    # ------------------------------------------------------------------ interpretation
    def _confirmed_error(self, read: StoveError | None) -> StoveError:
        """A change of the error register is only accepted after two consistent readings."""
        previous = self._last_error or NO_ERROR
        if read is None:
            return previous
        if self._last_error is None or read.code == self._last_error.code:
            self._candidate_error = None
            return read
        if self._candidate_error is not None and self._candidate_error.code == read.code:
            self._candidate_error = None
            return read
        self._candidate_error = read
        self._recheck()
        _LOGGER.debug("Error register changed to %s: waiting for a second reading", read.code)
        return previous

    def _apply(self, s: dict[str, Any]) -> dict[str, Any]:
        mirror = s.get("source") == "mirror"
        error = s["error"] if mirror else self._confirmed_error(None if "error" in s["missing"] else s.get("error"))
        alarm = error.code != 0
        status = "alarm" if alarm else s["status"]
        if self._last_error is not None and alarm != (self._last_error.code != 0):
            if alarm:
                self._fire("stove_alarm_raised", {"code": error.code, "error": error.id, "message": error.fr})
            else:
                self._fire("stove_alarm_cleared", {})
        self._last_error = error
        if self._last_status is not None and status != self._last_status:
            self._fire("stove_status_changed", {"status": status, "previous": self._last_status})
        self._last_status = status
        return {**s, "status": status, "error": error, "alarm": alarm}

    def _recheck(self) -> None:
        async_call_later(self.hass, RECHECK_DELAY, lambda _now: self.hass.async_create_task(self.async_request_refresh()))

    def _fire(self, kind: str, data: dict[str, Any]) -> None:
        _LOGGER.info("Event %s %s", kind, data)
        self.hass.bus.async_fire(EVENT, {"type": kind, "entry_id": self.entry.entry_id, **data})
