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


async def test_commands_guarded_then_sent(hass):
    coord = await _setup(hass)
    sim = coord.transport
    assert hass.states.get("switch.poele_test_commandes_autorisees") or hass.states.get("switch.poele_test_commands_allowed")
    sw = next(s.entity_id for s in hass.states.async_all("switch"))
    stop = next(s.entity_id for s in hass.states.async_all("button") if s.entity_id.endswith(("stop", "arreter")))
    assert hass.states.get(stop).state == "unavailable"
    with pytest.raises(HomeAssistantError):
        await coord.async_command("power", 0, label="Arrêter")
    assert sim.writes == []

    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(stop).state != "unavailable"

    assert len(hass.states.async_all("number")) == 1  # power only, no setpoint
    with pytest.raises(HomeAssistantError):
        await coord.async_command("setpoint", 21, label="Consigne")
    assert sim.writes == []
    assert not any("ambient" in s.entity_id or "ambiance" in s.entity_id for s in hass.states.async_all("sensor"))

    coord.data["status"] = "heating"
    await hass.services.async_call("button", "press", {"entity_id": stop}, blocking=True)
    assert sim.writes[-1] == "F0000"

    coord.data["status"] = "off"
    power = next(s.entity_id for s in hass.states.async_all("number") if "power" in s.entity_id or "puissance" in s.entity_id)
    await hass.services.async_call("number", "set_value", {"entity_id": power, "value": 4}, blocking=True)
    assert sim.writes[-1] == "F0000"  # stored, not sent while off
    start = next(s.entity_id for s in hass.states.async_all("button") if s.entity_id.endswith(("start", "allumer")))
    await hass.services.async_call("button", "press", {"entity_id": start}, blocking=True)
    assert sim.writes[-1] == "F0040"
    with pytest.raises(HomeAssistantError):
        coord.data["status"] = "heating"
        await hass.services.async_call("button", "press", {"entity_id": start}, blocking=True)

    await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    assert coord.commands_enabled is False
    await hass.config_entries.async_unload(coord.entry.entry_id)
