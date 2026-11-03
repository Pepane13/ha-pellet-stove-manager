"""Consumables state persistence across restarts (0.3.5; needs pytest-homeassistant-custom-component)."""

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.const import EVENT_HOMEASSISTANT_STOP  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: E402


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations):
    import custom_components

    repo = str(Path(__file__).resolve().parents[1] / "custom_components")
    if repo not in list(custom_components.__path__):
        custom_components.__path__.append(repo)
    yield


def _coord():
    fired = []
    return SimpleNamespace(
        entry=SimpleNamespace(entry_id="0123456789abcdef", title="Poêle test"),
        _fire=lambda kind, data: fired.append(kind),
        async_update_listeners=lambda: None,
        fired=fired,
    )


async def _manager(hass):
    from custom_components.pellet_stove_manager.manager import ConsumablesManager

    m = ConsumablesManager(hass, _coord(), {})
    await m.async_start()
    return m


def _stored(hass_storage, m):
    entry = hass_storage.get(m.store.key)
    return entry["data"] if entry else None


async def test_user_action_is_written_at_once(hass, hass_storage):
    m = await _manager(hass)
    m.refilled(remaining_percent=20, source="test")
    await hass.async_block_till_done()
    assert _stored(hass_storage, m) is not None
    assert _stored(hass_storage, m) == m.c.data
    await m.async_stop()


async def test_clock_changes_are_written_every_5_minutes(hass, hass_storage):
    m = await _manager(hass)
    m._tick(dt_util.now())  # silent change: marks the state dirty, no write yet
    await hass.async_block_till_done()
    assert m._dirty
    assert _stored(hass_storage, m) is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1))
    await hass.async_block_till_done()
    assert not m._dirty
    assert _stored(hass_storage, m) == m.c.data
    await m.async_stop()


async def test_state_survives_a_home_assistant_restart(hass, hass_storage):
    m = await _manager(hass)
    m.c.data["probe"] = "kept"  # change not yet written
    m._save()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert _stored(hass_storage, m)["probe"] == "kept"
    await m.async_stop()  # the fired stop listener must not be removed twice

    m2 = await _manager(hass)
    assert m2.c.data.get("probe") == "kept"
    await m2.async_stop()
