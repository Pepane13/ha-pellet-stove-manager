"""Pellets, cleaning, maintenance and burn time (port of lib/consumables.js of the Homey app).

Pure Python, no Home Assistant import: the coordinator feeds it stove snapshots and clock ticks,
stores ``data`` and turns the returned events into notifications.

Pellets: the tank is full after « tank refilled ». Consumption is integrated while the stove burns
(ignition, heating, eco) from the nominal rate of the power level (0.95 kg/h at 1 → 2.33 kg/h at 5,
Interstoves Diego 9) times a correction factor, plus an extra load at each ignition (the auger fills
the burn pot): with short Netatmo cycles (16 ignitions in two days on 1–2/11/2026) the rate alone
under-estimated a tank by ≈ 40 %. When a real level is known (empty tank, alarm 5, or the level seen
before refilling, half weight), the gap with the rate model is first explained by the ignitions
(≥ 3 since the last full tank: kg per ignition learnt), otherwise by the factor.

Refill advice: at the evening time (21:00) the tank must last until the morning (08:00); when the
house is opened in the morning it must last until the evening; reminder 45 min after an advice
without a refill; « tank low » held back overnight; at closing time (from 18:00) an alert if the tank
will not last until the morning. Daily need from the outdoor temperature (owner's two seasons):
heating hours/day = 15.06 − 0.809 T, kg per heating hour = 1.341 − 0.0282 T.

Cleaning: burning hours since the last cleaning; threshold fixed (``cleaning_refills`` = 0, 20 h since
02/11/2026) or ``cleaning_refills`` × the mean burning hours of a tank; pre-alert at 80 %. Reminders
are only given at a convenient time: weekday evenings (19:00) or weekend mornings (10:00), then every
such slot while the cleaning is due.
Maintenance: 12 months after the last service or 1,200 h of the stove's service counter.
Burn time: minutes per stove state per day, ignitions per day.
"""

from __future__ import annotations

import calendar
import copy
from datetime import date, datetime, timedelta
from typing import Any

DEFAULTS: dict[str, Any] = {
    "tank_kg": 19.0,
    "bag_kg": 15.0,
    "bag_price": 5.49,
    "low_kg": 4.0,
    "advice_time": "21:00",
    "morning_time": "08:00",
    "reminder_minutes": 45,
    "advice_margin": 1.2,
    "cleaning_hours": 44.0,       # fallback threshold until two refills are known
    "cleaning_refills": 3,        # cleaning due after this many tanks (mean burning hours per tank)
    "cleaning_prealert": 0.8,
    "cleaning_slot_weekday": "19:00",  # cleaning reminders on weekday evenings...
    "cleaning_slot_weekend": "10:00",  # ...or on weekend mornings (owner's choice, 02/11/2026)
    "ignition_kg": 0.15,          # extra pellets loaded at each ignition (prior, learnt on refills)
    "service_months": 12,
    "service_hours": 1200,
    "remind_days": 30,
    "remind_hours": 100,
}

RATE_MIN = 0.95
RATE_MAX = 2.33
BURNING = frozenset(("ignition", "heating", "eco"))
MAX_GAP_H = 0.25  # a longer gap between two snapshots is not integrated (stove unreachable)
FACTOR_PRIOR_WEIGHT = 2
FACTOR_MAX_WEIGHT = 10
NORMALS = {1: 8.0, 2: 9.8, 3: 11.9, 4: 15.1, 5: 18.8, 6: 23, 7: 26, 8: 26, 9: 21.5, 10: 16.5, 11: 11.8, 12: 8.9}
MONTHS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")

SEED_JOURNAL = [
    {"date": "2024-09-12", "type": "installation", "hours_total": 0, "hours_service": 0, "cost": 4507.72,
     "note": "Installation et mise en service (Interstoves France, Aubagne)"},
    {"date": "2025-08-19", "type": "service", "hours_total": 866, "hours_service": 866, "cost": 210,
     "note": "Entretien complet + ramonage"},
    {"date": "2026-08-27", "type": "service", "hours_total": 1723, "hours_service": 857, "cost": 210,
     "note": "Entretien complet + ramonage (facture : 1 753 h, erreur de saisie)"},
]


def rate_per_hour(power: Any) -> float:
    try:
        p = min(5, max(1, int(power)))
    except (TypeError, ValueError):
        p = 1
    return RATE_MIN + (RATE_MAX - RATE_MIN) * (p - 1) / 4


def kg_per_day(outdoor: float | None) -> float | None:
    if outdoor is None:
        return None
    hours = 15.06 - 0.809 * outdoor
    hours = max(0.5, hours) if outdoor < 19.5 else max(0.0, hours)
    return min(24.0, hours) * max(0.6, 1.341 - 0.0282 * outdoor)


def _hhmm(text: str) -> int:
    h, m = str(text).split(":")
    return int(h) * 60 + int(m)


def _r(x: float | None, n: int = 1) -> float | None:
    return None if x is None else round(x, n)


def add_months(day: str, months: int) -> str:
    d = date.fromisoformat(day)
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()


def empty_data(now: datetime) -> dict[str, Any]:
    ts = now.timestamp()
    return {
        "version": 1,
        "pellets": {"refill_at": ts, "kg_burned": 0.0, "model_kg": 0.0, "hours": 0.0, "full": False, "ignitions": 0,
                    "factor": {"value": 1.0, "n": 0.0}, "ignition_kg": {"value": None, "n": 0.0},
                    "low_notified": False, "refills": [], "empties": []},
        "cleaning": {"at": ts, "hours": 0.0, "notified": False, "pre_notified": False, "journal": []},
        "maintenance": {"last_date": "2026-08-27", "last_hours_total": 1723, "notified_soon": False,
                        "notified_due": False, "journal": copy.deepcopy(SEED_JOURNAL)},
        "months": {},
        "burn": {},  # "YYYY-MM-DD": {"minutes": {state: min}, "ignitions": n, "kg": kg}
        "daily": {"advice_day": None, "advised_at": None, "opened_day": None, "closed_night": None},
        "last": None,
    }


class Consumables:
    def __init__(self, data: dict[str, Any] | None, config: dict[str, Any] | None, now: datetime) -> None:
        self.data = data or empty_data(now)
        base = empty_data(now)
        for key, value in base.items():  # forward-compatible load
            if isinstance(value, dict):
                self.data[key] = {**value, **(self.data.get(key) or {})}
            else:
                self.data.setdefault(key, value)
        self.cfg = {**DEFAULTS, **(config or {})}
        self.events: list[tuple[str, dict[str, Any]]] = []

    # ------------------------------------------------------------------ helpers
    def _emit(self, kind: str, **data: Any) -> None:
        self.events.append((kind, data))

    def take_events(self) -> list[tuple[str, dict[str, Any]]]:
        ev, self.events = self.events, []
        return ev

    def _quiet(self, now: datetime) -> bool:
        """Overnight window: one hour after the evening advice until the house is opened
        (at the latest two hours after the usual opening time)."""
        m = now.hour * 60 + now.minute
        start = _hhmm(self.cfg["advice_time"]) + 60
        end = _hhmm(self.cfg["morning_time"]) + 120
        if self.data["daily"].get("opened_day") == now.date().isoformat():
            end = min(end, m)
        return m >= start or m < end

    def kg_left(self) -> float:
        return max(0.0, self.cfg["tank_kg"] - self.data["pellets"]["kg_burned"])

    def need_until(self, now: datetime, until: datetime, outdoor: float | None) -> float | None:
        daily = kg_per_day(outdoor)
        if daily is None:
            return None
        return daily * self.data["pellets"]["factor"]["value"] / 24 * max(0.0, (until - now).total_seconds() / 3600)

    # ------------------------------------------------------------------ observation
    def observe(self, now: datetime, status: str, power: Any, error_code: int,
                hours_total: int | None, hours_service: int | None) -> None:
        last = self.data["last"]
        day = now.date().isoformat()
        burn = self.data["burn"].setdefault(day, {"minutes": {}, "ignitions": 0, "kg": 0.0})
        if last:
            dt = (now.timestamp() - last["t"]) / 3600
            if 0 < dt <= MAX_GAP_H:
                burn["minutes"][last["status"]] = round(burn["minutes"].get(last["status"], 0) + dt * 60, 2)
                if last["status"] in BURNING:
                    pel = self.data["pellets"]
                    model = rate_per_hour(last["power"]) * dt
                    kg = model * pel["factor"]["value"]
                    pel["kg_burned"] += kg
                    pel["model_kg"] += model
                    pel["hours"] = pel.get("hours", 0.0) + dt
                    self.data["cleaning"]["hours"] += dt
                    burn["kg"] = round(burn["kg"] + kg, 3)
                    mk = now.strftime("%Y-%m")
                    self.data["months"][mk] = round(self.data["months"].get(mk, 0) + kg, 3)
            if status == "ignition" and last["status"] != "ignition":
                burn["ignitions"] += 1
                pel = self.data["pellets"]
                pel["ignitions"] = pel.get("ignitions", 0) + 1
                pel["kg_burned"] += self.ignition_kg()
                burn["kg"] = round(burn["kg"] + self.ignition_kg(), 3)
                mk = now.strftime("%Y-%m")
                self.data["months"][mk] = round(self.data["months"].get(mk, 0) + self.ignition_kg(), 3)
            if error_code == 5 and last.get("error") != 5:
                self._on_empty(now)
        self.data["last"] = {"t": now.timestamp(), "status": status, "power": power, "error": error_code,
                             "hours_total": hours_total, "hours_service": hours_service}
        self._prune_burn(now)
        self._check_low(now)
        self._check_cleaning()
        self._check_maintenance(now)

    def _prune_burn(self, now: datetime) -> None:
        keep = (now - timedelta(days=400)).date().isoformat()
        for k in [k for k in self.data["burn"] if k < keep]:
            del self.data["burn"][k]

    def ignition_kg(self) -> float:
        v = self.data["pellets"].get("ignition_kg", {}).get("value")
        return float(self.cfg["ignition_kg"] if v is None else v)

    def _learn_level(self, observed_kg: float, weight: float, event: dict[str, Any]) -> None:
        """A real consumption is known since the last full tank: learn kg per ignition, else the factor."""
        pel = self.data["pellets"]
        if not pel["full"] or observed_kg < self.cfg["tank_kg"] * 0.25 or pel["model_kg"] <= 0:
            return
        n = pel.get("ignitions", 0)
        rate_kg = pel["model_kg"] * pel["factor"]["value"]
        if n >= 3:
            obs = min(1.0, max(0.0, (observed_kg - rate_kg) / n))
            ik = pel.setdefault("ignition_kg", {"value": None, "n": 0.0})
            prior = self.ignition_kg()
            w = min(ik["n"] + FACTOR_PRIOR_WEIGHT, FACTOR_MAX_WEIGHT)
            ik["value"] = (prior * w + obs * weight) / (w + weight)
            ik["n"] += weight
            event["ignition_kg"] = round(obs, 3)
            event["ignitions"] = n
        elif self._learn(observed_kg / pel["model_kg"], weight):
            event["factor"] = round(observed_kg / pel["model_kg"], 3)

    def _on_empty(self, now: datetime) -> None:
        pel = self.data["pellets"]
        event = {"at": now.timestamp(), "model_kg": _r(pel["model_kg"], 2), "ignitions": pel.get("ignitions", 0),
                 "hours_since_refill": _r((now.timestamp() - pel["refill_at"]) / 3600)}
        self._learn_level(self.cfg["tank_kg"], 1, event)
        pel["empties"] = [event, *pel["empties"]][:20]
        pel["kg_burned"] = self.cfg["tank_kg"]
        self._emit("pellets_empty", **event)

    def _learn(self, obs: float, weight: float) -> bool:
        f = self.data["pellets"]["factor"]
        if not 0.4 < obs < 2.5:
            return False
        w = min(f["n"] + FACTOR_PRIOR_WEIGHT, FACTOR_MAX_WEIGHT)
        f["value"] = (f["value"] * w + obs * weight) / (w + weight)
        f["n"] += weight
        return True

    # ------------------------------------------------------------------ user actions
    def refilled(self, now: datetime, remaining_percent: float | None = None) -> dict[str, Any]:
        pel = self.data["pellets"]
        tank = self.cfg["tank_kg"]
        entry = {"at": now.timestamp(), "remaining_percent": remaining_percent,
                 "estimated_percent": round(self.kg_left() / tank * 100), "model_kg": _r(pel["model_kg"], 2),
                 "hours": _r(pel.get("hours", 0.0), 2), "after_full": pel["full"]}
        entry["ignitions"] = pel.get("ignitions", 0)
        if remaining_percent is not None:
            if not 0 <= remaining_percent <= 100:
                raise ValueError(f"Invalid level {remaining_percent}")
            self._learn_level(tank * (1 - remaining_percent / 100), 0.5, entry)
        pel["refills"] = [entry, *pel["refills"]][:50]
        pel.update(refill_at=now.timestamp(), kg_burned=0.0, model_kg=0.0, hours=0.0, ignitions=0, full=True,
                   low_notified=False)
        self.data["daily"]["advised_at"] = None
        return entry

    def set_level(self, percent: float) -> None:
        if not 0 <= percent <= 100:
            raise ValueError(f"Invalid level {percent}")
        self.data["pellets"]["kg_burned"] = self.cfg["tank_kg"] * (1 - percent / 100)
        if self.kg_left() > self.cfg["low_kg"]:
            self.data["pellets"]["low_notified"] = False

    def cleaned(self, now: datetime) -> None:
        c = self.data["cleaning"]
        c["journal"] = [{"at": now.timestamp(), "hours": _r(c["hours"], 2)}, *c.get("journal", [])][:50]
        c.update(at=now.timestamp(), hours=0.0, notified=False, pre_notified=False, reminded_day=None)

    def serviced(self, now: datetime, note: str = "", cost: float | None = None) -> None:
        m = self.data["maintenance"]
        last = self.data["last"] or {}
        d = now.date().isoformat()
        m["journal"].append({"date": d, "type": "service", "hours_total": last.get("hours_total"),
                             "hours_service": last.get("hours_service"), "cost": cost, "note": note})
        m.update(last_date=d, last_hours_total=last.get("hours_total"), notified_soon=False, notified_due=False)

    def import_state(self, now: datetime, kg_left: float | None = None, cleaning_hours: float | None = None,
                     factor: float | None = None, full: bool | None = None,
                     last_service_date: str | None = None, last_service_hours: int | None = None) -> None:
        pel = self.data["pellets"]
        if kg_left is not None:
            pel["kg_burned"] = max(0.0, self.cfg["tank_kg"] - kg_left)
        if factor is not None:
            pel["factor"] = {"value": float(factor), "n": max(pel["factor"]["n"], 1.0)}
        if full is not None:
            pel["full"] = bool(full)
        if cleaning_hours is not None:
            self.data["cleaning"]["hours"] = float(cleaning_hours)
        if last_service_date:
            self.data["maintenance"]["last_date"] = last_service_date
        if last_service_hours is not None:
            self.data["maintenance"]["last_hours_total"] = int(last_service_hours)

    def import_homey(self, now: datetime, homey: dict[str, Any], full: bool | None = None,
                     ignitions: int | None = None) -> dict[str, Any]:
        """Takes over the state learnt by the Homey app (settings key « consumables »)."""
        hp = homey.get("pellets") or {}
        pel = self.data["pellets"]
        if "factor" in hp:
            pel["factor"] = {"value": float(hp["factor"].get("value", 1)), "n": float(hp["factor"].get("n", 0))}
        if "kgBurned" in hp:
            pel["kg_burned"] = float(hp["kgBurned"])
        pel["model_kg"] = float(hp.get("modelKg", pel["model_kg"]) or 0)
        pel["full"] = bool(hp.get("full", pel["full"])) if full is None else bool(full)
        if ignitions is not None:
            pel["ignitions"] = int(ignitions)
        if hp.get("refillAt"):
            pel["refill_at"] = float(hp["refillAt"]) / 1000
        mean_rate = rate_per_hour(2)
        pel["hours"] = pel["model_kg"] / mean_rate
        refills = []
        for e in hp.get("refills") or []:
            refills.append({"at": float(e.get("at", 0)) / 1000, "remaining_percent": e.get("remainingPercent"),
                            "estimated_percent": e.get("estimatedPercent"), "model_kg": e.get("modelKg"),
                            "hours": round(float(e.get("modelKg") or 0) / mean_rate, 2), "after_full": True,
                            "factor": e.get("factor"), "source": "homey"})
        if refills:
            pel["refills"] = refills
        pel["empties"] = [{"at": float(e.get("at", 0)) / 1000, "model_kg": e.get("modelKg"),
                           "hours_since_refill": e.get("hoursSinceRefill"), "factor": e.get("factor"), "source": "homey"}
                          for e in hp.get("empties") or []] or pel["empties"]
        hc = homey.get("cleaning") or {}
        if "hours" in hc:
            self.data["cleaning"]["hours"] = float(hc["hours"])
        if hc.get("at"):
            self.data["cleaning"]["at"] = float(hc["at"]) / 1000
        hm = homey.get("maintenance") or {}
        if hm.get("lastDate"):
            self.data["maintenance"]["last_date"] = hm["lastDate"]
        for k, v in (homey.get("months") or {}).items():
            self.data["months"][k] = round(float(v), 3)
        return {"kg_left": _r(self.kg_left()), "factor": round(pel["factor"]["value"], 3),
                "refills": len(pel["refills"]), "cleaning_hours": _r(self.data["cleaning"]["hours"])}

    def add_burn(self, hours: float, power: int = 2, ignitions: int = 0) -> None:
        """Burning time the integration did not observe (e.g. while neither Homey nor HA read the stove)."""
        pel = self.data["pellets"]
        model = rate_per_hour(power) * hours
        pel["ignitions"] = pel.get("ignitions", 0) + ignitions
        pel["kg_burned"] += model * pel["factor"]["value"] + ignitions * self.ignition_kg()
        pel["model_kg"] += model
        pel["hours"] = pel.get("hours", 0.0) + hours
        self.data["cleaning"]["hours"] += hours

    # ------------------------------------------------------------------ alerts
    def _tokens(self, need: float | None = None) -> dict[str, Any]:
        left = self.kg_left()
        return {"kg_left": _r(left), "percent": round(left / self.cfg["tank_kg"] * 100),
                "kg_needed": _r(need or 0)}

    def _check_low(self, now: datetime) -> None:
        pel = self.data["pellets"]
        if self.kg_left() > self.cfg["low_kg"] or pel["low_notified"] or self._quiet(now):
            return
        pel["low_notified"] = True
        self._emit("pellets_low", **self._tokens())

    def tank_hours(self) -> tuple[float | None, int]:
        """Mean burning hours of a tank: refills that followed a known full tank, burnt for > 2 h."""
        samples = [e["hours"] for e in self.data["pellets"]["refills"][:10]
                   if e.get("after_full") and e.get("hours") and e["hours"] > 2]
        return (sum(samples) / len(samples) if samples else None), len(samples)

    def cleaning_threshold(self) -> float:
        mean, n = self.tank_hours()
        if self.cfg["cleaning_refills"] <= 0 or mean is None or n < 2:
            return float(self.cfg["cleaning_hours"])
        return round(mean * self.cfg["cleaning_refills"], 1)

    def _check_cleaning(self) -> None:
        """Nothing is raised here: reminders wait for the next cleaning slot (see tick)."""

    def _cleaning_slot(self, now: datetime) -> bool:
        key = "cleaning_slot_weekend" if now.weekday() >= 5 else "cleaning_slot_weekday"
        return now.hour * 60 + now.minute >= _hhmm(self.cfg[key])

    def _cleaning_reminder(self, now: datetime) -> None:
        c = self.data["cleaning"]
        thr = self.cleaning_threshold()
        day = now.date().isoformat()
        if c.get("reminded_day") == day or not self._cleaning_slot(now):
            return
        if c["hours"] >= thr:
            c["notified"] = True
            c["reminded_day"] = day
            self._emit("cleaning_due", hours=_r(c["hours"]), threshold=thr)
        elif c["hours"] >= thr * self.cfg["cleaning_prealert"] and not c["pre_notified"]:
            c["pre_notified"] = True
            c["reminded_day"] = day
            self._emit("cleaning_soon", hours=_r(c["hours"]), threshold=thr)

    def maintenance_state(self, now: datetime) -> dict[str, Any]:
        m = self.data["maintenance"]
        last = self.data["last"] or {}
        due_date = add_months(m["last_date"], self.cfg["service_months"])
        days_left = (date.fromisoformat(due_date) - now.date()).days
        hs = last.get("hours_service")
        ht = last.get("hours_total")
        since = ht - m["last_hours_total"] if ht is not None and m.get("last_hours_total") is not None else hs
        hours_left = self.cfg["service_hours"] - since if since is not None else None
        hours_due = None
        elapsed = (now.date() - date.fromisoformat(m["last_date"])).days
        if since and since > 20 and elapsed > 30 and hours_left is not None:
            hours_due = (now.date() + timedelta(days=hours_left / (since / elapsed))).isoformat()
        nxt = hours_due if hours_due and hours_due < due_date else due_date
        return {"last_date": m["last_date"], "due_date": due_date, "days_left": days_left,
                "hours_since": since, "hours_left": hours_left, "next_date": nxt,
                "due": days_left <= 0 or (hours_left is not None and hours_left <= 0),
                "soon": days_left <= self.cfg["remind_days"] or (hours_left is not None and hours_left <= self.cfg["remind_hours"]),
                "journal": sorted(m["journal"], key=lambda e: e["date"], reverse=True)}

    def _check_maintenance(self, now: datetime) -> None:
        """Alerts once per crossing. An hour-based « due » must hold on two distinct stove readings
        (a corrupted counter must not raise it); flags are released when the state goes back to normal."""
        m = self.data["maintenance"]
        s = self.maintenance_state(now)
        date_due = s["days_left"] <= 0
        reading = (self.data["last"] or {}).get("t")
        if s["due"] and not date_due:
            seen = m.setdefault("due_readings", [])
            if reading is not None and reading not in seen:
                seen.append(reading)
                del seen[:-2]
            confirmed = len(seen) >= 2
        else:
            m["due_readings"] = []
            confirmed = s["due"]
        if not s["due"]:
            m["notified_due"] = False
        if not s["soon"]:
            m["notified_soon"] = False
        if confirmed and not m["notified_due"]:
            m["notified_due"] = True
            self._emit("maintenance_due", date=s["due_date"], days_left=s["days_left"], hours_left=s["hours_left"])
        elif s["soon"] and not s["due"] and not m["notified_soon"]:
            m["notified_soon"] = True
            self._emit("maintenance_soon", date=s["due_date"], days_left=s["days_left"], hours_left=s["hours_left"])

    # ------------------------------------------------------------------ clock
    def tick(self, now: datetime, outdoor: float | None) -> None:
        d = self.data["daily"]
        cfg = self.cfg
        m = now.hour * 60 + now.minute
        day = now.date().isoformat()
        advice = _hhmm(cfg["advice_time"])
        if m >= advice and d["advice_day"] != day:
            d["advice_day"] = day
            morning = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, minutes=_hhmm(cfg["morning_time"]))
            need = self.need_until(now, morning, outdoor)
            if need is not None and self.kg_left() < need * cfg["advice_margin"]:
                d["advised_at"] = now.timestamp()
                if self.kg_left() <= cfg["low_kg"]:
                    self.data["pellets"]["low_notified"] = True
                self._emit("pellets_refill_advised", moment="evening", **self._tokens(need))
            else:
                d["advised_at"] = None
                self._emit("pellets_refill_not_needed", moment="evening", **self._tokens(need))
        elif d["advised_at"] and cfg["reminder_minutes"] and now.timestamp() - d["advised_at"] >= cfg["reminder_minutes"] * 60:
            d["advised_at"] = None
            self._emit("pellets_refill_advised", moment="reminder", **self._tokens())
        self._check_low(now)
        self._check_maintenance(now)
        self._cleaning_reminder(now)

    def house_opened(self, now: datetime, outdoor: float | None) -> bool:
        d = self.data["daily"]
        day = now.date().isoformat()
        advice = _hhmm(self.cfg["advice_time"])
        if d.get("opened_day") == day or now.hour * 60 + now.minute >= advice:
            return False
        d["opened_day"] = day
        until = now.replace(hour=advice // 60, minute=advice % 60, second=0, microsecond=0)
        need = self.need_until(now, until, outdoor)
        advised = need is not None and self.kg_left() < need * self.cfg["advice_margin"]
        if advised:
            if self.kg_left() <= self.cfg["low_kg"]:
                self.data["pellets"]["low_notified"] = True
            self._emit("pellets_refill_advised", moment="morning", **self._tokens(need))
        self._check_low(now)
        return advised

    def house_closed(self, now: datetime, outdoor: float | None) -> bool:
        m = now.hour * 60 + now.minute
        morning_m = _hhmm(self.cfg["morning_time"])
        evening = m >= 18 * 60
        if not evening and m >= morning_m:
            return False
        night = (now if evening else now - timedelta(days=1)).date().isoformat()
        d = self.data["daily"]
        if d.get("closed_night") == night:
            return False
        base = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=morning_m)
        until = base + timedelta(days=1) if evening else base
        need = self.need_until(now, until, outdoor)
        if need is None or self.kg_left() >= need * self.cfg["advice_margin"]:
            return False
        d["closed_night"] = night
        d["advised_at"] = None
        if evening:
            d["advice_day"] = night
        if self.kg_left() <= self.cfg["low_kg"]:
            self.data["pellets"]["low_notified"] = True
        self._emit("pellets_refill_missing", **self._tokens(need))
        return True

    # ------------------------------------------------------------------ read models
    def pellet_state(self, now: datetime, outdoor: float | None) -> dict[str, Any]:
        pel = self.data["pellets"]
        left = self.kg_left()
        last = self.data["last"] or {}
        burning = last.get("status") in BURNING
        rate_now = rate_per_hour(last.get("power")) * pel["factor"]["value"] if burning else None
        daily = kg_per_day(outdoor)
        daily = daily * pel["factor"]["value"] if daily is not None else None
        hours_left = left / (daily / 24) if daily else None
        mk = now.strftime("%Y-%m")
        normal = NORMALS[now.month]
        days = calendar.monthrange(now.year, now.month)[1]
        f_kg = round((kg_per_day(normal) or 0) * pel["factor"]["value"] * days)
        return {
            "kg_left": _r(left), "percent": round(left / self.cfg["tank_kg"] * 100),
            "rate_now": _r(rate_now, 2), "hours_at_current_power": _r(left / rate_now) if rate_now else None,
            "kg_per_day": _r(daily), "empty_at": now + timedelta(hours=hours_left) if hours_left is not None and hours_left < 24 * 14 else None,
            "factor": round(pel["factor"]["value"], 3), "factor_samples": pel["factor"]["n"], "known": pel["full"],
            "ignition_kg": round(self.ignition_kg(), 3), "ignition_kg_samples": pel.get("ignition_kg", {}).get("n", 0),
            "ignitions_since_refill": pel.get("ignitions", 0),
            "refill_at": pel["refill_at"], "refills": pel["refills"][:5], "empties": pel["empties"][:5],
            "month_kg": _r(self.data["months"].get(mk, 0), 1),
            "month_cost": round(self.data["months"].get(mk, 0) / self.cfg["bag_kg"] * self.cfg["bag_price"], 2),
            "month_forecast_kg": f_kg, "month_forecast_bags": -(-f_kg // int(self.cfg["bag_kg"])) if f_kg else 0,
            "month_label": f"{MONTHS_FR[now.month - 1]} {now.year}",
        }

    def cleaning_state(self) -> dict[str, Any]:
        c = self.data["cleaning"]
        thr = self.cleaning_threshold()
        mean, n = self.tank_hours()
        return {"hours": _r(c["hours"]), "threshold": thr, "due": c["hours"] >= thr, "at": c["at"],
                "tank_hours": _r(mean), "tank_samples": n, "refills_per_cleaning": self.cfg["cleaning_refills"],
                "threshold_source": "learnt" if mean is not None and n >= 2 else "default",
                "journal": c.get("journal", [])[:5]}

    def burn_today(self, now: datetime) -> dict[str, Any]:
        b = self.data["burn"].get(now.date().isoformat()) or {"minutes": {}, "ignitions": 0, "kg": 0.0}
        mins = b["minutes"]
        burning = sum(v for k, v in mins.items() if k in BURNING)
        return {"burning_hours": round(burning / 60, 2), "ignition_hours": round(mins.get("ignition", 0) / 60, 2),
                "heating_hours": round((mins.get("heating", 0) + mins.get("eco", 0)) / 60, 2),
                "ignitions": b["ignitions"], "kg": b["kg"],
                "minutes": {k: round(v) for k, v in mins.items()}}

    def burn_month(self, now: datetime) -> dict[str, Any]:
        prefix = now.strftime("%Y-%m")
        total: dict[str, float] = {}
        ign = 0
        for k, b in self.data["burn"].items():
            if k.startswith(prefix):
                ign += b["ignitions"]
                for s, v in b["minutes"].items():
                    total[s] = total.get(s, 0) + v
        burning = sum(v for k, v in total.items() if k in BURNING)
        return {"burning_hours": round(burning / 60, 2), "ignitions": ign,
                "hours_by_state": {k: round(v / 60, 2) for k, v in total.items()}}
