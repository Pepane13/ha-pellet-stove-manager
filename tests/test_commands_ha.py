"""Command entities in Home Assistant, simulator mode (needs pytest-homeassistant-custom-component)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

DOMAIN = "pellet_stove_manager"


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations):
    import custom_components

    repo = str(Path(__file__).resolve().parents[1] / "custom_components")
    if repo not in list(custom_components.__path__):
        custom_components.__path__.append(repo)
    yield


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Poêle test", data={"mode": "simulator", "poll_interval": 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def test_v03_entities_commands_and_records(hass):
    coord = await _setup(hass)
    sim = coord.transport
    ids = [s.entity_id for s in hass.states.async_all()]
    assert not any(i.endswith(("_allumer", "_start", "_arreter", "_stop")) for i in ids)
    assert any("granules_restants" in i or "pellets_left" in i for i in ids)
    sw = next(s.entity_id for s in hass.states.async_all("switch"))
    power = next(s.entity_id for s in hass.states.async_all("number"))

    # power: guarded by the switch, and only while burning
    coord.data["status"] = "off"
    with pytest.raises(HomeAssistantError):
        await coord.async_set_power(3)
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    with pytest.raises(HomeAssistantError):
        await coord.async_set_power(3)  # stove off: would ignite it
    coord.data["status"] = "heating"
    await hass.services.async_call("number", "set_value", {"entity_id": power, "value": 3}, blocking=True)
    assert sim.writes[-1] == "F0030"
    with pytest.raises(HomeAssistantError):
        await coord.async_command("setpoint", 21, label="Consigne")

    # alarm reset policy
    from custom_components.pellet_stove_manager.duepi.protocol import StoveError

    coord.data.update(alarm=True, error=StoveError(9, "exhaust_motor", "", "Moteur d'extraction", 3), phase="off")
    assert "grave" in coord.reset_refusal()
    coord.data.update(error=StoveError(5, "out_of_pellets", "", "Manque de pellets", 1), phase="cooling")
    assert "éteint" in coord.reset_refusal()
    coord.data["phase"] = "off"
    assert coord.reset_refusal() is None
    await coord.async_reset_alarm(source="test")
    assert sim.writes[-1] == "D6000"

    # records: button and notification action
    m = coord.manager
    m.c.set_level(30)
    refill = next(s.entity_id for s in hass.states.async_all("button") if "sac" in s.entity_id or "refilled" in s.entity_id)
    await hass.services.async_call("button", "press", {"entity_id": refill}, blocking=True)
    assert m.c.kg_left() == 19
    m.c.data["cleaning"]["hours"] = 12
    hass.bus.async_fire("mobile_app_notification_action", {"action": f"PSM_CLEANED_{coord.entry.entry_id[-6:]}"})
    await hass.async_block_till_done()
    assert m.c.data["cleaning"]["hours"] == 0

    # Homey import service
    res = await hass.services.async_call(
        "pellet_stove_manager", "import_homey",
        {"consumables": {"pellets": {"kgBurned": 9.1, "factor": {"value": 1.1, "n": 2}, "full": True}, "cleaning": {"hours": 6.8}},
         "extra_burn_hours": 0.5, "extra_power": 2},
        blocking=True, return_response=True)
    assert res["kg_left"] == 9.9 and res["kg_left_after_extra"] == round(9.9 - 0.5 * 1.295 * 1.1, 1)
    await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    await hass.config_entries.async_unload(coord.entry.entry_id)
