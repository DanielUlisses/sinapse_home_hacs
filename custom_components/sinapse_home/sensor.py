"""Sensors: read-only numeric params (water temperature for history/graphs)."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, P_TEMPERATURE
from .entity import SinapseEntity
from .generic import is_readable, is_writable, iter_extra_params


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SinapseSensor(coordinator, node_id, device, param)
        for node_id, device, param in iter_extra_params(coordinator)
        if param.get("data_type") in ("int", "float")
        and is_readable(param)
        and not is_writable(param)
    )


class SinapseSensor(SinapseEntity, SensorEntity):
    """Generic read-only numeric param."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, node_id, device, param) -> None:
        super().__init__(
            coordinator, node_id, device, param["name"], f"{device['name']} {param['name']}"
        )
        self._param = param
        if param.get("type") == P_TEMPERATURE:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float | int | None:
        return self._value(self._param)
