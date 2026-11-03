from counters import CounterGuard

T0 = 1_762_000_000.0


def snap(total, service):
    return {"status": "off", "hours_total": total, "hours_service": service}


def test_normal_progress_and_gap():
    g = CounterGuard()
    out, rej = g.check(snap(1739, 16), T0)
    assert (out["hours_total"], out["hours_service"], rej) == (1739, 16, [])
    out, rej = g.check(snap(1740, 17), T0 + 3600)
    assert out["hours_total"] == 1740 and not rej
    # link down 10 h, stove burnt 8 h meanwhile: accepted
    out, rej = g.check(snap(1748, 25), T0 + 11 * 3600)
    assert out["hours_total"] == 1748 and out["hours_service"] == 25 and not rej


def test_night_of_03_11_corrupted_frames():
    g = CounterGuard()
    g.check(snap(1739, 16), T0)
    out, rej = g.check(snap(0, 0), T0 + 60)  # 00:16 / 01:57
    assert out["hours_total"] == 1739 and out["hours_service"] == 16 and len(rej) == 2
    for i in range(5):  # repeated zero frames never accepted
        out, _ = g.check(snap(0, 0), T0 + 120 + 60 * i)
        assert (out["hours_total"], out["hours_service"]) == (1739, 16)
    out, rej = g.check(snap(None, 8448), T0 + 600)  # 01:40: total missing, service 8 448
    assert out["hours_service"] == 16 and out["hours_total"] == 1739 and rej
    out, rej = g.check(snap(1739, 16), T0 + 660)
    assert out["hours_service"] == 16 and not rej


def test_service_reset_confirmed():
    g = CounterGuard()
    g.check(snap(1739, 1200), T0)
    for i in range(2):
        out, rej = g.check(snap(1739, 0), T0 + 60 * (i + 1))
        assert out["hours_service"] == 1200 and rej
    out, rej = g.check(snap(1739, 0), T0 + 180)
    assert out["hours_service"] == 0 and not rej


def test_total_never_goes_back():
    g = CounterGuard()
    g.check(snap(1739, 16), T0)
    for i in range(4):
        out, _ = g.check(snap(1500, 16), T0 + 60 * (i + 1))
        assert out["hours_total"] == 1739
