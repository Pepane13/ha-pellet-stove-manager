"""Stove sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import REVOLUTIONS_PER_MINUTE, EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STATUS_OPTIONS
from .coordinator import StoveCoordinator
from .entity import StoveEntity


@dataclass(frozen=True, kw_only=True)
class StoveSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]


def _get(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda d: d.get(key)


SENSORS: tuple[StoveSensorDescription, ...] = (
    StoveSensorDescription(
        key="stove_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(STATUS_OPTIONS),
        value=_get("status"),
    ),
    StoveSensorDescription(key="stove_error", value=lambda d: d["error"].fr if d["alarm"] else "—"),
    StoveSensorDescription(key="power_level", state_class=SensorStateClass.MEASUREMENT, value=_get("power_level")),
    StoveSensorDescription(
        key="flue_gas_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value=_get("flue_gas"),
    ),
    StoveSensorDescription(
        key="board_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=_get("board"),
    ),
    StoveSensorDescription(
        key="ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,  # 99.9 °C = no probe (external thermostat)
        value=_get("ambient"),
    ),
    StoveSensorDescription(
        key="auger_speed",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=_get("auger"),
    ),
    StoveSensorDescription(
        key="exhaust_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=_get("exhaust"),
    ),
    StoveSensorDescription(
        key="hours_total",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value=_get("hours_total"),
    ),
    StoveSensorDescription(
        key="hours_service",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value=_get("hours_service"),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    add(StoveSensor(coordinator, d) for d in SENSORS)


class StoveSensor(StoveEntity, SensorEntity):
    entity_description: StoveSensorDescription

    def __init__(self, coordinator: StoveCoordinator, description: StoveSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._key == "stove_error" and self.coordinator.data:
            e = self.coordinator.data["error"]
            return {"code": e.code, "error_id": e.id, "severity": e.severity}
        if self._key == "stove_status":
            return {"source": self.coordinator.mode}
        return None
