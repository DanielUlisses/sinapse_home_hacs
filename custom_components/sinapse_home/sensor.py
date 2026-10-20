"""Sensors: read-only numeric params (water temperature for history/graphs) and the
heater setpoint, so setpoint changes (from HA, the app or voice) show up in the logbook."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DEV_THERMOSTAT,
    DOMAIN,
    P_SETPOINT,
    P_TEMPERATURE,
)
from .coordinator import SinapseCoordinator
from .entity import SinapseEntity, find_param, iter_devices, node_device_info
from .generic import is_readable, is_writable, iter_extra_params


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        SinapseSensor(coordinator, node_id, device, param)
        for node_id, device, param in iter_extra_params(coordinator)
        if param.get("data_type") in ("int", "float")
        and is_readable(param)
        and not is_writable(param)
    ]
    entities.extend(
        SinapseSetpointSensor(coordinator, node_id, device)
        for node_id, device in iter_devices(coordinator)
        if device.get("type") == DEV_THERMOSTAT and find_param(device, P_SETPOINT)
    )
    entities.extend(
        SinapseConnectionSensor(coordinator, node_id) for node_id in coordinator.data
    )
    async_add_entities(entities)


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


class SinapseSetpointSensor(SinapseEntity, SensorEntity):
    """Heater setpoint as a state, so every change is recorded in the logbook.

    On the climate entity the setpoint is only an attribute, which the logbook
    ignores; the spa also starts the panel and pump whenever it changes.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, node_id, device) -> None:
        super().__init__(
            coordinator, node_id, device, "setpoint", f"{device['name']} Temperatura Alvo"
        )
        self._setpoint = find_param(device, P_SETPOINT)

    @property
    def native_value(self) -> float | int | None:
        return self._value(self._setpoint)


class SinapseConnectionSensor(CoordinatorEntity[SinapseCoordinator], SensorEntity):
    """How the node is reached right now: over the LAN ("local") or the cloud."""

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [CONNECTION_LOCAL, CONNECTION_CLOUD]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SinapseCoordinator, node_id: str) -> None:
        super().__init__(coordinator)
        self._node_id = node_id
        self._attr_unique_id = f"{node_id}_connection"
        self._attr_device_info = node_device_info(coordinator, node_id)

    @property
    def native_value(self) -> str:
        return self.coordinator.connection(self._node_id)
