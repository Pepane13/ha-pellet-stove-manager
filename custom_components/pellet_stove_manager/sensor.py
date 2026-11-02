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
from homeassistant.const import PERCENTAGE, REVOLUTIONS_PER_MINUTE, EntityCategory, UnitOfMass, UnitOfTemperature, UnitOfTime
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarms import advice_for
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
        key="pressure_switch",
        device_class=SensorDeviceClass.ENUM,
        options=["normal", "pressure"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.get("pressure_switch") if d.get("pressure_switch") in ("normal", "pressure") else None,
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


@dataclass(frozen=True, kw_only=True)
class ConsumableSensorDescription(SensorEntityDescription):
    value: Callable[[Any], Any]
    attrs: Callable[[Any], dict[str, Any]] | None = None


def _p(key: str) -> Callable[[Any], Any]:
    return lambda m: m.c.pellet_state(dt_util.now(), m.outdoor())[key]


CONSUMABLES: tuple[ConsumableSensorDescription, ...] = (
    ConsumableSensorDescription(
        key="pellets_left", native_unit_of_measurement=UnitOfMass.KILOGRAMS, device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT, icon="mdi:sack", value=_p("kg_left"),
        attrs=lambda m: {k: v for k, v in m.c.pellet_state(dt_util.now(), m.outdoor()).items()
                         if k in ("factor", "factor_samples", "known", "rate_now", "kg_per_day", "refills", "empties",
                                  "ignition_kg", "ignition_kg_samples", "ignitions_since_refill")},
    ),
    ConsumableSensorDescription(key="pellets_percent", native_unit_of_measurement=PERCENTAGE, icon="mdi:gauge",
                                state_class=SensorStateClass.MEASUREMENT, value=_p("percent")),
    ConsumableSensorDescription(key="pellets_empty_at", device_class=SensorDeviceClass.TIMESTAMP, icon="mdi:clock-alert",
                                value=_p("empty_at")),
    ConsumableSensorDescription(key="pellets_autonomy", native_unit_of_measurement=UnitOfTime.HOURS,
                                device_class=SensorDeviceClass.DURATION, icon="mdi:timer-sand",
                                value=_p("hours_at_current_power")),
    ConsumableSensorDescription(key="pellets_month_kg", native_unit_of_measurement=UnitOfMass.KILOGRAMS,
                                device_class=SensorDeviceClass.WEIGHT, state_class=SensorStateClass.MEASUREMENT,
                                icon="mdi:calendar-month", value=_p("month_kg")),
    ConsumableSensorDescription(key="pellets_month_cost", native_unit_of_measurement="EUR",
                                device_class=SensorDeviceClass.MONETARY, icon="mdi:currency-eur", value=_p("month_cost")),
    ConsumableSensorDescription(key="pellets_month_forecast", native_unit_of_measurement="sacs", icon="mdi:chart-line",
                                value=_p("month_forecast_bags"),
                                attrs=lambda m: {"kg": m.c.pellet_state(dt_util.now(), m.outdoor())["month_forecast_kg"]}),
    ConsumableSensorDescription(key="cleaning_hours", native_unit_of_measurement=UnitOfTime.HOURS,
                                device_class=SensorDeviceClass.DURATION, state_class=SensorStateClass.MEASUREMENT,
                                icon="mdi:broom", value=lambda m: m.c.cleaning_state()["hours"],
                                attrs=lambda m: {k: v for k, v in m.c.cleaning_state().items() if k != "hours"}),
    ConsumableSensorDescription(key="cleaning_threshold", native_unit_of_measurement=UnitOfTime.HOURS,
                                device_class=SensorDeviceClass.DURATION, icon="mdi:broom",
                                entity_category=EntityCategory.DIAGNOSTIC,
                                value=lambda m: m.c.cleaning_state()["threshold"]),
    ConsumableSensorDescription(key="maintenance_next", device_class=SensorDeviceClass.DATE, icon="mdi:wrench-clock",
                                value=lambda m: dt_util.parse_date(m.c.maintenance_state(dt_util.now())["next_date"]),
                                attrs=lambda m: {k: v for k, v in m.c.maintenance_state(dt_util.now()).items() if k != "next_date"}),
    ConsumableSensorDescription(key="maintenance_hours_left", native_unit_of_measurement=UnitOfTime.HOURS,
                                device_class=SensorDeviceClass.DURATION, icon="mdi:wrench-clock",
                                value=lambda m: m.c.maintenance_state(dt_util.now())["hours_left"]),
    ConsumableSensorDescription(key="burn_today", native_unit_of_measurement=UnitOfTime.HOURS,
                                device_class=SensorDeviceClass.DURATION, icon="mdi:fire",
                                value=lambda m: m.c.burn_today(dt_util.now())["burning_hours"],
                                attrs=lambda m: m.c.burn_today(dt_util.now())),
    ConsumableSensorDescription(key="burn_month", native_unit_of_measurement=UnitOfTime.HOURS,
                                device_class=SensorDeviceClass.DURATION, icon="mdi:fire",
                                value=lambda m: m.c.burn_month(dt_util.now())["burning_hours"],
                                attrs=lambda m: m.c.burn_month(dt_util.now())),
    ConsumableSensorDescription(key="ignitions_today", icon="mdi:fire-circle", state_class=SensorStateClass.MEASUREMENT,
                                value=lambda m: m.c.burn_today(dt_util.now())["ignitions"]),
)


class ConsumableSensor(StoveEntity, SensorEntity):
    entity_description: ConsumableSensorDescription

    def __init__(self, coordinator: StoveCoordinator, description: ConsumableSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.coordinator.manager is not None and self.coordinator.manager.c is not None

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        f = self.entity_description.attrs
        return f(self.coordinator.manager) if f else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: StoveCoordinator = hass.data[DOMAIN][entry.entry_id]
    add(StoveSensor(coordinator, d) for d in SENSORS)
    if coordinator.manager is not None:
        add(ConsumableSensor(coordinator, d) for d in CONSUMABLES)


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
            adv = advice_for(e.code) if e.code else None
            return {"code": e.code, "error_id": e.id, "severity": e.severity,
                    "advice": adv.advice if adv else None, "resettable": adv.resettable if adv else None}
        if self._key == "stove_status":
            return {"source": self.coordinator.mode}
        return None
