"""Pellet Stove Manager: Duepi EVO pellet stoves (port of the Homey app com.pepane13.pelletstove)."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import StoveCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.NUMBER]

ENTRY = vol.Optional("entry_id")
SCHEMAS = {
    "refilled": vol.Schema({ENTRY: str, vol.Optional("remaining_percent"): vol.All(vol.Coerce(float), vol.Range(0, 100))}),
    "set_pellet_level": vol.Schema({ENTRY: str, vol.Required("percent"): vol.All(vol.Coerce(float), vol.Range(0, 100))}),
    "cleaned": vol.Schema({ENTRY: str}),
    "serviced": vol.Schema({ENTRY: str, vol.Optional("note", default=""): str, vol.Optional("cost"): vol.Coerce(float)}),
    "import_homey": vol.Schema({ENTRY: str, vol.Required("consumables"): vol.Any(dict, str),
                                vol.Optional("full"): bool, vol.Optional("ignitions"): vol.Coerce(int),
                                vol.Optional("extra_burn_hours", default=0): vol.Coerce(float),
                                vol.Optional("extra_ignitions", default=0): vol.Coerce(int),
                                vol.Optional("extra_power", default=2): vol.All(vol.Coerce(int), vol.Range(1, 5))}),
    "add_burn": vol.Schema({ENTRY: str, vol.Required("hours"): vol.Coerce(float),
                            vol.Optional("ignitions", default=0): vol.Coerce(int),
                            vol.Optional("power", default=2): vol.All(vol.Coerce(int), vol.Range(1, 5))}),
}


def _manager(hass: HomeAssistant, call: ServiceCall):
    coords: dict[str, StoveCoordinator] = hass.data.get(DOMAIN, {})
    eid = call.data.get("entry_id")
    coord = coords.get(eid) if eid else next((c for c in coords.values() if c.manager is not None), None)
    if coord is None or coord.manager is None:
        raise HomeAssistantError("No connected stove (pellet tracking needs the cloud or local mode)")
    return coord.manager


async def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "refilled"):
        return

    async def refilled(call: ServiceCall) -> None:
        _manager(hass, call).refilled(call.data.get("remaining_percent"), source="service")

    async def set_level(call: ServiceCall) -> None:
        m = _manager(hass, call)
        m.c.set_level(call.data["percent"])
        m.update()

    async def cleaned(call: ServiceCall) -> None:
        _manager(hass, call).cleaned(source="service")

    async def serviced(call: ServiceCall) -> None:
        _manager(hass, call).serviced(call.data.get("note", ""), call.data.get("cost"))

    async def import_homey(call: ServiceCall) -> dict[str, Any]:
        m = _manager(hass, call)
        data = call.data["consumables"]
        if isinstance(data, str):
            data = json.loads(data)
        result = m.c.import_homey(dt_util.now(), data, call.data.get("full"), call.data.get("ignitions"))
        if call.data["extra_burn_hours"] or call.data["extra_ignitions"]:
            m.c.add_burn(call.data["extra_burn_hours"], call.data["extra_power"], call.data["extra_ignitions"])
        m._log(f"état repris de l'app Homey : {result}")
        m.update()
        return {**result, "kg_left_after_extra": round(m.c.kg_left(), 1),
                "cleaning_hours_after_extra": round(m.c.data["cleaning"]["hours"], 1)}

    async def add_burn(call: ServiceCall) -> None:
        m = _manager(hass, call)
        m.c.add_burn(call.data["hours"], call.data["power"], call.data["ignitions"])
        m.update()

    for name, handler in (("refilled", refilled), ("set_pellet_level", set_level), ("cleaned", cleaned),
                          ("serviced", serviced), ("add_burn", add_burn)):
        hass.services.async_register(DOMAIN, name, handler, schema=SCHEMAS[name])
    hass.services.async_register(DOMAIN, "import_homey", import_homey, schema=SCHEMAS["import_homey"],
                                 supports_response=SupportsResponse.OPTIONAL)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = StoveCoordinator(hass, entry)
    await coordinator.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: StoveCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()
    return ok


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
