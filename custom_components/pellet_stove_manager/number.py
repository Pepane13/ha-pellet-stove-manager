"""Stove numbers: power level and setpoint."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, HEATING_STATES
from .coordinator import StoveCoordinator
from .entity import StoveEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.can_command:
        add([PowerNumber(coordinator), SetpointNumber(coordinator)])


class _CommandNumber(StoveEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.connected and self.coordinator.commands_enabled


class PowerNumber(_CommandNumber):
    """Sends the level while the stove burns; when it is off, keeps it for the next « Start »."""

    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        if data.get("status") in HEATING_STATES and data.get("power_level"):
            return data["power_level"]
        return self.coordinator.requested_power or data.get("power_level")

    async def async_set_native_value(self, value: float) -> None:
        level = int(value)
        self.coordinator.requested_power = level
        if (self.coordinator.data or {}).get("status") in HEATING_STATES:
            await self.coordinator.async_command("power", level, label=f"Puissance {level}")
        else:
            self.async_write_ha_state()


class SetpointNumber(_CommandNumber):
    _attr_icon = "mdi:thermometer"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = 5
    _attr_native_max_value = 35
    _attr_native_step = 1

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "setpoint")
        self._sent: int | None = None

    @property
    def native_value(self) -> float | None:
        read = (self.coordinator.data or {}).get("setpoint")
        return read if read is not None else self._sent

    async def async_set_native_value(self, value: float) -> None:
        target = int(value)
        await self.coordinator.async_command("setpoint", target, label=f"Consigne {target} °C")
        self._sent = target
        self.async_write_ha_state()
