"""Pellets, cleaning, maintenance and alarm notifications around the coordinator.

- Persists the Consumables state (Home Assistant Store), feeds it every stove snapshot and a
  one-minute clock tick, follows the « house opened » entity (morning / evening checks).
- Turns events into notifications (persistent notification + notify services), with action buttons
  « Rechargé », « Nettoyé », « Acquitter ». Notifications are held while the house is closed (night)
  and sent when it opens; severity-3 alarms are sent at once.
- Every decision is logged in the logbook and raised as a ``pellet_stove_manager_event``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .alarms import advice_for
from .consumables import Consumables
from .const import (
    CONF_BAG_KG,
    CONF_BAG_PRICE,
    CONF_CLEANING_DEFAULT_H,
    CONF_CLEANING_REFILLS,
    CONF_HOUSE_OPEN,
    CONF_NOTIFY,
    CONF_OUTDOOR,
    CONF_TANK_KG,
    DOMAIN,
)

if TYPE_CHECKING:
    from .coordinator import StoveCoordinator

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1

ACTION_REFILLED = "PSM_REFILLED"
ACTION_CLEANED = "PSM_CLEANED"
ACTION_RESET = "PSM_RESET_ALARM"

TEXTS = {
    "pellets_refill_advised": ("Poêle : recharger les granulés",
                               "Il reste {kg_left} kg ({percent} %) ; il en faudra environ {kg_needed} kg {when}."),
    "pellets_refill_missing": ("Poêle : le réservoir ne tiendra pas la nuit",
                               "Il reste {kg_left} kg ({percent} %) pour environ {kg_needed} kg jusqu'au matin."),
    "pellets_low": ("Poêle : réservoir bas", "Il reste {kg_left} kg ({percent} %)."),
    "pellets_empty": ("Poêle : réservoir vide", "Alarme « manque de pellets » : recharger, vider le creuset, acquitter."),
    "cleaning_soon": ("Poêle : nettoyage bientôt", "{hours} h de combustion sur {threshold} h."),
    "cleaning_due": ("Poêle : nettoyage à faire", "{hours} h de combustion depuis le dernier nettoyage (seuil {threshold} h)."),
    "maintenance_soon": ("Poêle : entretien annuel à prévoir", "Échéance le {date} ({days_left} jours, {hours_left} h restantes)."),
    "maintenance_due": ("Poêle : entretien annuel dépassé", "Échéance le {date} : appeler l'installateur."),
}
WHEN = {"evening": "d'ici demain matin", "morning": "d'ici ce soir", "reminder": "d'ici demain matin"}
NOTIFY_EVENTS = set(TEXTS)
ACTIONS = {
    "pellets_refill_advised": [(ACTION_REFILLED, "Rechargé")],
    "pellets_refill_missing": [(ACTION_REFILLED, "Rechargé")],
    "pellets_low": [(ACTION_REFILLED, "Rechargé")],
    "pellets_empty": [(ACTION_REFILLED, "Rechargé")],
    "cleaning_soon": [(ACTION_CLEANED, "Nettoyé")],
    "cleaning_due": [(ACTION_CLEANED, "Nettoyé")],
}


class ConsumablesManager:
    def __init__(self, hass: HomeAssistant, coordinator: StoveCoordinator, options: dict[str, Any]) -> None:
        self.hass = hass
        self.coord = coordinator
        self.entry = coordinator.entry
        self.options = options
        self.store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{self.entry.entry_id}.consumables")
        self.c: Consumables | None = None
        self._unsub: list = []
        self._held: list[tuple[str, str, str, list]] = []

    # ------------------------------------------------------------------ lifecycle
    def _config(self) -> dict[str, Any]:
        o = self.options
        cfg: dict[str, Any] = {}
        for key, name in ((CONF_TANK_KG, "tank_kg"), (CONF_BAG_KG, "bag_kg"), (CONF_BAG_PRICE, "bag_price"),
                          (CONF_CLEANING_DEFAULT_H, "cleaning_hours"), (CONF_CLEANING_REFILLS, "cleaning_refills")):
            if o.get(key) not in (None, ""):
                cfg[name] = float(o[key])
        for key in ("cleaning_slot_weekday", "cleaning_slot_weekend"):
            if o.get(key):
                cfg[key] = str(o[key])[:5]
        return cfg

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        self.c = Consumables(stored, self._config(), dt_util.now())
        self._unsub.append(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))
        house = self.options.get(CONF_HOUSE_OPEN)
        if house:
            self._unsub.append(async_track_state_change_event(self.hass, [house], self._on_house))
        self._unsub.append(self.hass.bus.async_listen("mobile_app_notification_action", self._on_action))

    async def async_stop(self) -> None:
        for u in self._unsub:
            u()
        self._unsub.clear()
        if self.c:
            await self.store.async_save(self.c.data)

    def _save(self) -> None:
        if self.c:
            self.store.async_delay_save(lambda: self.c.data, 30)

    # ------------------------------------------------------------------ inputs
    def outdoor(self) -> float | None:
        ent = self.options.get(CONF_OUTDOOR)
        st = self.hass.states.get(ent) if ent else None
        try:
            return float(st.state) if st else None
        except ValueError:
            return None

    def house_open(self) -> bool | None:
        ent = self.options.get(CONF_HOUSE_OPEN)
        st = self.hass.states.get(ent) if ent else None
        if st is None or st.state in ("unavailable", "unknown"):
            return None
        return st.state == STATE_ON

    @callback
    def observe(self, data: dict[str, Any]) -> None:
        if not self.c:
            return
        self.c.observe(dt_util.now(), data.get("phase") or data["status"], data.get("power_level"),
                       data["error"].code, data.get("hours_total"), data.get("hours_service"))
        self._flush()

    @callback
    def _tick(self, _now: Any) -> None:
        if self.c:
            self.c.tick(dt_util.now(), self.outdoor())
            self._flush()

    @callback
    def _on_house(self, event: Event) -> None:
        new = event.data.get("new_state")
        old = event.data.get("old_state")
        if not self.c or new is None or old is None or new.state == old.state:
            return
        if new.state == STATE_ON:
            self.c.house_opened(dt_util.now(), self.outdoor())
            self._flush()
            self._release_held()
        elif new.state == "off":
            self.c.house_closed(dt_util.now(), self.outdoor())
            self._flush()

    # ------------------------------------------------------------------ user actions
    def refilled(self, remaining_percent: float | None = None, source: str = "bouton") -> None:
        assert self.c
        e = self.c.refilled(dt_util.now(), remaining_percent)
        self._log(f"recharge enregistrée ({source}), niveau estimé avant {e['estimated_percent']} %"
                  + (f", vu {remaining_percent:.0f} %" if remaining_percent is not None else ""))
        self.coord._fire("pellets_refilled", {"source": source, **{k: v for k, v in e.items() if k != "at"}})
        self._flush()

    def cleaned(self, source: str = "bouton") -> None:
        assert self.c
        hours = self.c.cleaning_state()["hours"]
        self.c.cleaned(dt_util.now())
        self._log(f"nettoyage enregistré ({source}) après {hours} h de combustion")
        self.coord._fire("cleaning_done", {"source": source, "hours": hours})
        self._flush()

    def serviced(self, note: str = "", cost: float | None = None) -> None:
        assert self.c
        self.c.serviced(dt_util.now(), note, cost)
        self._log("entretien annuel enregistré")
        self.coord._fire("maintenance_done", {"note": note, "cost": cost})
        self._flush()

    def update(self) -> None:
        self._flush()

    @callback
    def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action", ""))
        if not action.endswith(self.entry.entry_id[-6:]):
            return
        if action.startswith(ACTION_REFILLED):
            self.refilled(source="notification")
        elif action.startswith(ACTION_CLEANED):
            self.cleaned(source="notification")
        elif action.startswith(ACTION_RESET):
            self.hass.async_create_task(self._reset_from_notification())

    async def _reset_from_notification(self) -> None:
        try:
            await self.coord.async_reset_alarm(source="notification")
        except Exception as err:  # noqa: BLE001
            await self._send("Poêle : acquittement refusé", str(err), [])

    # ------------------------------------------------------------------ alarms
    @callback
    def alarm_raised(self, code: int, message: str, severity: int) -> None:
        adv = advice_for(code)
        actions = [(ACTION_RESET, "Acquitter")] if adv.resettable else []
        title = f"Poêle : alarme {code} – {message}"
        self._log(f"alarme {code} ({message}) : {adv.advice}")
        self._notify(title, adv.advice, actions, urgent=severity >= 3)

    @callback
    def alarm_cleared(self) -> None:
        self._log("alarme terminée")
        self._notify("Poêle : alarme terminée", "Le poêle n'a plus d'alarme active.", [])

    # ------------------------------------------------------------------ output
    @callback
    def _flush(self) -> None:
        if not self.c:
            return
        for kind, data in self.c.take_events():
            self.coord._fire(kind, data)
            if kind in ("pellets_refill_not_needed",):
                self._log(f"conseil du {data.get('moment')} : pas de recharge nécessaire "
                          f"({data.get('kg_left')} kg pour {data.get('kg_needed')} kg)")
                continue
            if kind in NOTIFY_EVENTS:
                title, body = TEXTS[kind]
                body = body.format(when=WHEN.get(data.get("moment"), ""), **{k: ("—" if v is None else v) for k, v in data.items()})
                self._log(f"{title} — {body}")
                self._notify(title, body, ACTIONS.get(kind, []))
        self._save()
        self.coord.async_update_listeners()

    def _log(self, message: str) -> None:
        if self.hass.services.has_service("logbook", "log"):
            self.hass.async_create_task(self.hass.services.async_call(
                "logbook", "log", {"name": self.entry.title, "message": message, "domain": DOMAIN}))

    @callback
    def _notify(self, title: str, body: str, actions: list, urgent: bool = False) -> None:
        if not urgent and self.house_open() is False:
            self._held.append((title, body, "held", actions))
            self._log(f"notification retenue jusqu'à l'ouverture de la maison : {title}")
            return
        self.hass.async_create_task(self._send(title, body, actions))

    @callback
    def _release_held(self) -> None:
        held, self._held = self._held, []
        for title, body, _, actions in held:
            self.hass.async_create_task(self._send(title, body, actions))

    async def _send(self, title: str, body: str, actions: list) -> None:
        suffix = self.entry.entry_id[-6:]
        targets = [t.strip().removeprefix("notify.") for t in str(self.options.get(CONF_NOTIFY) or "").split(",") if t.strip()]
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {"title": title, "message": body, "notification_id": f"{DOMAIN}_{suffix}_{abs(hash(title)) % 10000}"})
        data = {"actions": [{"action": f"{a}_{suffix}", "title": t} for a, t in actions]} if actions else {}
        for target in targets:
            if not self.hass.services.has_service("notify", target):
                _LOGGER.warning("Notify service notify.%s not found", target)
                continue
            try:
                await self.hass.services.async_call("notify", target, {"title": title, "message": body, "data": data})
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Notification to %s failed: %s", target, err)
