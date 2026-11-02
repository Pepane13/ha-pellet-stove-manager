"""Stove number: power level, sent only while the stove burns (Netatmo drives start and stop)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, HEATING_STATES
from .coordinator import StoveCoordinator
from .entity import StoveEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.can_command:
        add([PowerNumber(coordinator)])


class PowerNumber(StoveEntity, NumberEntity):
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def available(self) -> bool:
        d = self.coordinator.data or {}
        return (super().available and self.coordinator.connected and self.coordinator.commands_enabled
                and d.get("status") in HEATING_STATES)

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("power_level")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_power(int(value))
