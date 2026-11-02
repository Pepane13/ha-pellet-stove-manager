"""Stove binary sensors (Homey conditions « is heating », « has an alarm », connection)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, HEATING_STATES
from .coordinator import StoveCoordinator
from .entity import StoveEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    c: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    add([HeatingSensor(c), AlarmSensor(c), ConnectionSensor(c)])


class HeatingSensor(StoveEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, c: StoveCoordinator) -> None:
        super().__init__(c, "heating")

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        return None if not d else d["status"] in HEATING_STATES


class AlarmSensor(StoveEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, c: StoveCoordinator) -> None:
        super().__init__(c, "alarm")

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        return None if not d else bool(d["alarm"])


class ConnectionSensor(StoveEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c: StoveCoordinator) -> None:
        super().__init__(c, "connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected and self.coordinator.last_update_success
