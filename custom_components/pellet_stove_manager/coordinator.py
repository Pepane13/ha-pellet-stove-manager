"""Stove coordinator: reads the stove (Duepi relay, local bridge, simulator) or mirrors
the Homey app through its homeyHASS entities, and raises the same events as the Homey
device's Flow triggers."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
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
    BURNING_STATES,
    HEATING_STATES,
    RECHECK_DELAY,
    UNAVAILABLE_AFTER,
)
from .alarms import advice_for
from .counters import CounterGuard
from .duepi.client import DesyncError, DuepiClient
from .duepi.protocol import ERRORS, StoveError, is_ack
from .duepi.simulator import SimulatedStove
from .duepi.transport import DuepiTransport

_LOGGER = logging.getLogger(__name__)

#: commands exposed by the integration; the setpoint is left to the external thermostat
ALLOWED_COMMANDS = ("power", "reset_alarm")

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
        #: set by the « Commands allowed » switch (restored, off by default); guards the power command
        self.commands_enabled = False
        self.options = opts
        self.manager = None  # ConsumablesManager, created in async_start for connected modes
        self._alarm_reset_at: dict[int, float] = {}
        self.counters = CounterGuard()

    # ------------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        if self.client is not None:
            from .manager import ConsumablesManager

            self.manager = ConsumablesManager(self.hass, self, self.options)
            await self.manager.async_start()
        if self.mode == MODE_MIRROR:
            ids = [t.format(p=self.prefix) for t in MIRROR.values()]
            self._unsub.append(async_track_state_change_event(self.hass, ids, self._on_mirror_change))
        await self.async_config_entry_first_refresh()

    async def async_stop(self) -> None:
        if self.manager is not None:
            await self.manager.async_stop()
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
            if self.data:
                self._recheck()
                return self.data
            # first reading (start-up, reload): stray frames from the previous session are common,
            # read again once before giving up
            await asyncio.sleep(3)
            try:
                state = await self.client.snapshot()
            except Exception as err2:  # noqa: BLE001
                raise UpdateFailed(str(err2)) from err2
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

    def _check_counters(self, s: dict[str, Any]) -> dict[str, Any]:
        """Hold back implausible hour counters (see counters.CounterGuard)."""
        out, rejected = self.counters.check(s, time.time())
        for key, value, kept in rejected:
            _LOGGER.warning("Implausible %s %s ignored (last accepted %s)", key, value, kept)
            self._fire("stove_counter_rejected", {"counter": key, "value": value, "kept": kept})
        return out

    def _apply(self, s: dict[str, Any]) -> dict[str, Any]:
        s = self._check_counters(s)
        mirror = s.get("source") == "mirror"
        error = s["error"] if mirror else self._confirmed_error(None if "error" in s["missing"] else s.get("error"))
        alarm = error.code != 0
        status = "alarm" if alarm else s["status"]
        if self._last_error is not None and alarm != (self._last_error.code != 0):
            if alarm:
                adv = advice_for(error.code)
                self._fire("stove_alarm_raised", {"code": error.code, "error": error.id, "message": error.fr,
                                                  "advice": adv.advice, "resettable": adv.resettable})
                if self.manager:
                    self.manager.alarm_raised(error.code, error.fr, error.severity)
            else:
                self._fire("stove_alarm_cleared", {})
                if self.manager:
                    self.manager.alarm_cleared()
        self._last_error = error
        if self._last_status is not None and status != self._last_status:
            self._fire("stove_status_changed", {"status": status, "previous": self._last_status})
        self._last_status = status
        data = {**s, "phase": s["status"], "status": status, "error": error, "alarm": alarm}
        if self.manager and not mirror:
            self.manager.observe(data)
        return data

    # ------------------------------------------------------------------ commands
    @property
    def can_command(self) -> bool:
        """Commands need a direct link to the stove (cloud, local or simulator)."""
        return self.client is not None

    def reset_refusal(self) -> str | None:
        """Why the alarm cannot be reset from Home Assistant right now (None = allowed)."""
        d = self.data or {}
        if not self.can_command:
            return "pas de liaison directe avec le poêle"
        if not d.get("alarm"):
            return "aucune alarme active"
        adv = advice_for(d["error"].code)
        if not adv.resettable:
            return "alarme grave : intervention sur place ou technicien, pas d'acquittement à distance"
        if d.get("phase") in BURNING_STATES or d.get("phase") == "cooling":
            return "attendre que le poêle soit éteint (refroidissement terminé)"
        return None

    async def async_reset_alarm(self, source: str = "bouton") -> str:
        refusal = self.reset_refusal()
        if refusal:
            raise HomeAssistantError(f"Acquittement refusé : {refusal}")
        code = self.data["error"].code
        reply = await self.async_command("reset_alarm", label=f"Acquitter l'alarme {code} ({source})", guarded=False)
        import time

        last = self._alarm_reset_at.get(code)
        self._alarm_reset_at[code] = time.time()
        if last and time.time() - last < 24 * 3600 and self.manager:
            self.manager._notify(f"Poêle : alarme {code} répétée",
                                 "Deuxième acquittement en moins de 24 h : appeler le technicien.", [])
        return reply

    async def async_set_power(self, level: int) -> str:
        if (self.data or {}).get("status") not in HEATING_STATES:
            raise HomeAssistantError(
                "Puissance envoyée seulement pendant la chauffe : sur un poêle éteint, la commande l'allumerait "
                "(Netatmo pilote la marche). Le poêle garde son dernier niveau.")
        return await self.async_command("power", level, label=f"Puissance {level}")

    async def async_command(self, name: str, *args: Any, label: str, guarded: bool = True) -> str:
        """Send one write command, guarded by the « Commands allowed » switch.

        Every command is logged (logbook + `stove_command` event) with its reply, then the
        stove is read again a few seconds later so the dashboard shows the real result.
        """
        if name not in ALLOWED_COMMANDS:
            raise HomeAssistantError(f"Command {name} is not allowed (the setpoint belongs to the thermostat)")
        if not self.can_command:
            raise HomeAssistantError("Commands need a direct link to the stove (cloud or local mode)")
        if guarded and not self.commands_enabled:
            raise HomeAssistantError("Stove commands are disabled: turn on « Commands allowed » first")
        assert self.client is not None
        try:
            reply = await self.client.write(name, *args)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid command {label}: {err}") from err
        except Exception as err:  # noqa: BLE001
            self._fire("stove_command", {"command": name, "args": list(args), "label": label, "ack": False, "error": str(err)})
            await self._logbook(f"commande « {label} » non envoyée : {err}")
            raise HomeAssistantError(f"Command {label} failed: {err}") from err
        ack = is_ack(reply)
        _LOGGER.info("Command %s %s -> %s (ack=%s)", name, args, reply, ack)
        self._fire("stove_command", {"command": name, "args": list(args), "label": label, "reply": reply, "ack": ack})
        await self._logbook(f"commande « {label} » envoyée au poêle, " + ("acquittée" if ack else f"réponse inattendue {reply}"))
        self._recheck()
        if not ack:
            raise HomeAssistantError(f"Command {label}: unexpected reply {reply}")
        return reply

    async def _logbook(self, message: str) -> None:
        if self.hass.services.has_service("logbook", "log"):
            await self.hass.services.async_call(
                "logbook", "log", {"name": self.entry.title, "message": message, "domain": DOMAIN}, blocking=False
            )

    def _recheck(self) -> None:
        async_call_later(self.hass, RECHECK_DELAY, self._recheck_now)

    @callback
    def _recheck_now(self, _now: Any) -> None:
        # @callback: runs in the event loop (a plain lambda would run in a worker thread)
        self.hass.async_create_task(self.async_request_refresh())

    def _fire(self, kind: str, data: dict[str, Any]) -> None:
        _LOGGER.info("Event %s %s", kind, data)
        self.hass.bus.async_fire(EVENT, {"type": kind, "entry_id": self.entry.entry_id, **data})
