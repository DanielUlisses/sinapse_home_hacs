"""Binary sensors: read-only boolean params (heater running, water level, cooling)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, P_COOLING, P_TOGGLE
from .entity import SinapseEntity
from .generic import is_readable, is_writable, iter_extra_params

DEVICE_CLASSES = {
    P_TOGGLE: BinarySensorDeviceClass.HEAT,
    P_COOLING: BinarySensorDeviceClass.COLD,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SinapseBinarySensor(coordinator, node_id, device, param)
        for node_id, device, param in iter_extra_params(coordinator)
        if param.get("data_type") == "bool" and is_readable(param) and not is_writable(param)
    )


class SinapseBinarySensor(SinapseEntity, BinarySensorEntity):
    """Generic read-only bool param."""

    def __init__(self, coordinator, node_id, device, param) -> None:
        super().__init__(
            coordinator, node_id, device, param["name"], f"{device['name']} {param['name']}"
        )
        self._param = param
        self._attr_device_class = DEVICE_CLASSES.get(param.get("type"))

    @property
    def is_on(self) -> bool | None:
        value = self._value(self._param)
        return None if value is None else bool(value)
