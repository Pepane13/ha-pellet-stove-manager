from datetime import datetime, timedelta, timezone

from consumables import Consumables, kg_per_day, rate_per_hour

TZ = timezone(timedelta(hours=1))


def t(h, m=0, d=2):
    return datetime(2026, 11, d, h, m, tzinfo=TZ)


def burn(c, start, minutes, power=2, status="heating"):
    for i in range(0, minutes + 1):
        c.observe(start + timedelta(minutes=i), status, power, 0, 1739, 16)


def test_rates_and_model():
    assert rate_per_hour(1) == 0.95 and rate_per_hour(5) == 2.33
    assert round(kg_per_day(8), 2) == round((15.06 - 0.809 * 8) * (1.341 - 0.0282 * 8), 2)


def test_integration_and_cleaning():
    c = Consumables(None, None, t(8))
    c.import_state(t(8), kg_left=10, cleaning_hours=35)
    burn(c, t(9), 60, power=1)
    assert round(c.kg_left(), 2) == round(10 - 0.95, 2)
    assert round(c.data["cleaning"]["hours"], 2) == 36.0
    assert "cleaning_soon" in [k for k, _ in c.take_events()]  # 36 h >= 0.8 x 44
    burn(c, t(10), 9 * 60, power=1)
    assert "cleaning_due" in [k for k, _ in c.take_events()]
    c.cleaned(t(16))
    assert c.cleaning_state()["hours"] == 0


def test_gap_not_integrated():
    c = Consumables(None, None, t(8))
    c.observe(t(9), "heating", 2, 0, 1, 1)
    c.observe(t(10), "heating", 2, 0, 1, 1)  # 1 h gap
    assert c.kg_left() == 19


def test_evening_advice_and_reminder():
    c = Consumables(None, None, t(8))
    c.import_state(t(8), kg_left=3)
    c.observe(t(20), "off", 1, 0, 1, 1)
    c.take_events()
    c.tick(t(21), outdoor=7.0)
    ev = c.take_events()
    assert ev[0][0] == "pellets_refill_advised" and ev[0][1]["moment"] == "evening"
    c.tick(t(21, 46), outdoor=7.0)
    assert c.take_events()[0][1]["moment"] == "reminder"
    c.refilled(t(21, 50))
    c.tick(t(22, 40), outdoor=7.0)
    assert c.take_events() == []
    assert c.kg_left() == 19


def test_low_held_overnight_released_on_opening():
    c = Consumables(None, None, t(8))
    c.import_state(t(8), kg_left=3)
    c.observe(t(23, 30), "off", 1, 0, 1, 1)
    assert "pellets_low" not in [k for k, _ in c.take_events()]
    c.house_opened(t(7, 30, d=3), outdoor=7.0)
    assert any(k == "pellets_refill_advised" for k, _ in c.take_events())


def test_empty_tank_learns_factor():
    c = Consumables(None, None, t(8))
    c.refilled(t(8))
    burn(c, t(8), 10 * 60, power=2)  # model 12.95 kg
    c.observe(t(18, 1), "off", 2, 5, 1, 1)
    ev = dict(c.take_events())
    assert "pellets_empty" in ev and c.data["pellets"]["factor"]["value"] > 1
    assert c.kg_left() == 0


def test_burn_stats_and_maintenance():
    c = Consumables(None, None, t(8))
    c.observe(t(9), "off", 2, 0, 1739, 16)
    burn(c, t(9, 1), 12, status="ignition")
    burn(c, t(9, 14), 10, status="heating")
    b = c.burn_today(t(10))
    assert b["ignitions"] == 1 and round(b["burning_hours"] * 60) == 23
    s = c.maintenance_state(t(10))
    assert s["due_date"] == "2027-08-27" and s["hours_since"] == 16 and s["hours_left"] == 1184


def test_cleaning_threshold_learnt_from_refills():
    c = Consumables(None, None, t(8))
    c.refilled(t(8))
    for day in (3, 4, 5):
        burn(c, t(8, d=day), 10 * 60, power=2)
        c.refilled(t(19, d=day))
    s = c.cleaning_state()
    assert s["threshold_source"] == "learnt" and s["threshold"] == 30.0 and s["tank_samples"] == 3


def test_import_homey():
    c = Consumables(None, None, t(8))
    r = c.import_homey(t(8), {"pellets": {"factor": {"value": 1.08, "n": 2.5}, "kgBurned": 9.1, "modelKg": 8.4, "full": True,
                                          "refillAt": 1793000000000, "refills": [{"at": 1792000000000, "modelKg": 13.0}]},
                              "cleaning": {"hours": 6.8, "at": 1792500000000}, "months": {"2026-11": 8.0}})
    assert r["kg_left"] == 9.9 and r["factor"] == 1.08 and r["cleaning_hours"] == 6.8
    c.add_burn(0.5, power=2)
    assert round(c.kg_left(), 2) == round(9.9 - 0.5 * 1.295 * 1.08, 2)
