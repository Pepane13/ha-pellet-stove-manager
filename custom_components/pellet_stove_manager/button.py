"""Stove buttons: start, stop, alarm reset."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StoveCoordinator
from .entity import StoveEntity

DEFAULT_START_POWER = 3


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.can_command:
        add([StartButton(coordinator), StopButton(coordinator), ResetAlarmButton(coordinator)])


class _CommandButton(StoveEntity, ButtonEntity):
    @property
    def available(self) -> bool:
        return super().available and self.coordinator.connected and self.coordinator.commands_enabled

    def _status(self) -> str | None:
        return (self.coordinator.data or {}).get("status")


class StartButton(_CommandButton):
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "start")

    async def async_press(self) -> None:
        status = self._status()
        if status != "off":
            raise HomeAssistantError(f"Start refused: the stove is {status}, not off")
        data = self.coordinator.data or {}
        level = self.coordinator.requested_power or data.get("power_level") or DEFAULT_START_POWER
        level = min(5, max(1, int(level)))
        await self.coordinator.async_command("power", level, label=f"Allumer (puissance {level})")


class StopButton(_CommandButton):
    _attr_icon = "mdi:fire-off"

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "stop")

    async def async_press(self) -> None:
        if self._status() == "off":
            raise HomeAssistantError("Stop refused: the stove is already off")
        await self.coordinator.async_command("power", 0, label="Arrêter")


class ResetAlarmButton(_CommandButton):
    _attr_icon = "mdi:alarm-light-off"

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "reset_alarm")

    async def async_press(self) -> None:
        await self.coordinator.async_command("reset_alarm", label="Acquitter l'alarme")
