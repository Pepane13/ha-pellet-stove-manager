"""Stove buttons: refill, cleaning and maintenance records, alarm reset.

Start and stop are not exposed: the Netatmo thermostat drives the stove (owner's decision, 02/11/2026).
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StoveCoordinator
from .entity import StoveEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.can_command:
        add([RefilledButton(coordinator), CleanedButton(coordinator), ServicedButton(coordinator),
             ResetAlarmButton(coordinator)])


class _RecordButton(StoveEntity, ButtonEntity):
    @property
    def available(self) -> bool:
        return self.coordinator.manager is not None


class RefilledButton(_RecordButton):
    _attr_icon = "mdi:sack"

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "refilled")

    async def async_press(self) -> None:
        self.coordinator.manager.refilled(source="bouton")


class CleanedButton(_RecordButton):
    _attr_icon = "mdi:broom"

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "cleaned")

    async def async_press(self) -> None:
        self.coordinator.manager.cleaned(source="bouton")


class ServicedButton(_RecordButton):
    _attr_icon = "mdi:wrench-check"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "serviced")

    async def async_press(self) -> None:
        self.coordinator.manager.serviced()


class ResetAlarmButton(StoveEntity, ButtonEntity):
    """Available only when the alarm may be reset remotely (see alarms.py)."""

    _attr_icon = "mdi:alarm-light-off"

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "reset_alarm")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.connected and self.coordinator.reset_refusal() is None

    async def async_press(self) -> None:
        await self.coordinator.async_reset_alarm(source="bouton")
