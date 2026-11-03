"""Plausibility guard for the stove hour counters (pure Python, no Home Assistant import).

On 03/11/2026, during relay drop-outs, corrupted Duepi frames gave a total counter of 0 h and a
service counter of 8 448 h (instead of 1 739 h and 16 h), which raised a false « service overdue »
alert. Rules:
- a counter only moves forward, by at most the elapsed time since the last accepted reading
  plus ``MARGIN_H`` (the stove counts whole hours; the link may have been down for a while);
- the total counter never goes back and is never 0;
- the service counter may legitimately go back (reset after a service, set on the stove): such a
  value is accepted after ``CONFIRM`` consistent readings (±1 h);
- when the total counter of a snapshot is rejected, its service counter is not trusted either.
Rejected values are replaced by the last accepted one (or None before the first one).
"""

from __future__ import annotations

from typing import Any

MARGIN_H = 2
CONFIRM = 3
KEYS = ("hours_total", "hours_service")


class CounterGuard:
    def __init__(self) -> None:
        self.accepted: dict[str, tuple[int, float]] = {}
        self.candidates: dict[str, list[int]] = {}
        self.rejected = 0

    def check(self, snapshot: dict[str, Any], now: float) -> tuple[dict[str, Any], list[tuple[str, Any, Any]]]:
        out = dict(snapshot)
        rejected: list[tuple[str, Any, Any]] = []
        frame_suspect = False
        for key in KEYS:
            if key not in snapshot:
                continue
            value = snapshot.get(key)
            last = self.accepted.get(key)
            kept = last[0] if last else None
            if value is None:
                out[key] = kept
                continue
            if frame_suspect and key == "hours_service":
                out[key] = kept
                rejected.append((key, value, kept))
                continue
            in_range = key != "hours_total" or value > 0
            if last is None:
                ok = in_range
            else:
                ok = in_range and last[0] <= value <= last[0] + (now - last[1]) / 3600 + MARGIN_H
            if not ok and key == "hours_service" and in_range:
                cands = self.candidates.setdefault(key, [])
                if cands and abs(value - cands[-1]) > 1:
                    cands.clear()
                cands.append(value)
                ok = len(cands) >= CONFIRM
            if ok:
                self.accepted[key] = (value, now)
                self.candidates.pop(key, None)
                out[key] = value
                continue
            if key == "hours_total":
                frame_suspect = True
            out[key] = kept
            rejected.append((key, value, kept))
        self.rejected += len(rejected)
        return out, rejected
