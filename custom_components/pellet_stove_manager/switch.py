"""« Commands allowed » switch: master guard for every write sent to the stove."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import StoveCoordinator
from .entity import StoveEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.can_command:
        add([CommandsAllowedSwitch(coordinator)])


class CommandsAllowedSwitch(StoveEntity, SwitchEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:shield-key"

    def __init__(self, coordinator: StoveCoordinator) -> None:
        super().__init__(coordinator, "commands_allowed")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.commands_enabled = last is not None and last.state == "on"

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.commands_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.commands_enabled = True
        self.coordinator._fire("stove_commands_allowed", {"allowed": True})
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.commands_enabled = False
        self.coordinator._fire("stove_commands_allowed", {"allowed": False})
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
