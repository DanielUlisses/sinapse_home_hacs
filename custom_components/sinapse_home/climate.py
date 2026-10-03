"""Climate: Aquecedor (any esp.device.thermostat)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEV_THERMOSTAT,
    DOMAIN,
    P_AC_MODE,
    P_COOLING,
    P_POWER,
    P_SETPOINT,
    P_TEMPERATURE,
    P_TOGGLE,
)
from .entity import SinapseEntity, find_param, iter_devices

# RainMaker ac-mode string <-> HA mode
MODE_TO_HA = {
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "auto": HVACMode.HEAT_COOL,
    "off": HVACMode.OFF,
}
HA_TO_MODE = {v: k for k, v in MODE_TO_HA.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SinapseClimate(coordinator, node_id, device)
        for node_id, device in iter_devices(coordinator)
        if device.get("type") == DEV_THERMOSTAT and find_param(device, P_SETPOINT)
    )


class SinapseClimate(SinapseEntity, ClimateEntity):
    """Spa water heater."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator, node_id, device) -> None:
        super().__init__(coordinator, node_id, device, "climate", device["name"])
        self._setpoint = find_param(device, P_SETPOINT)
        self._temp = find_param(device, P_TEMPERATURE)
        self._mode = find_param(device, P_AC_MODE)
        self._power = find_param(device, P_POWER)
        self._toggle = find_param(device, P_TOGGLE)
        self._cooling = find_param(device, P_COOLING)

        bounds = self._setpoint.get("bounds", {})
        self._attr_min_temp = bounds.get("min", 20)
        self._attr_max_temp = bounds.get("max", 40)
        self._attr_target_temperature_step = bounds.get("step", 1)

        # Only offer modes the device declares (valid_strs); default to heat only.
        modes: list[HVACMode] = []
        if self._mode:
            for value in self._mode.get("valid_strs") or ["heat"]:
                if value in MODE_TO_HA and MODE_TO_HA[value] not in modes:
                    modes.append(MODE_TO_HA[value])
        if not modes:
            modes = [HVACMode.HEAT]
        if self._power and HVACMode.OFF not in modes:
            modes.append(HVACMode.OFF)
        self._attr_hvac_modes = modes

        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._power:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        self._attr_supported_features = features

    @property
    def current_temperature(self) -> float | None:
        return self._value(self._temp)

    @property
    def target_temperature(self) -> float | None:
        return self._value(self._setpoint)

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self._power and self._value(self._power) is False:
            return HVACMode.OFF
        mode = self._value(self._mode)
        if mode is None:
            return self._attr_hvac_modes[0]
        return MODE_TO_HA.get(str(mode).lower(), HVACMode.HEAT)

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._value(self._cooling):
            return HVACAction.COOLING
        if self._value(self._toggle):
            return HVACAction.HEATING
        return HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self._write({self._setpoint["name"]: int(round(temp))})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        values: dict[str, Any] = {}
        if hvac_mode == HVACMode.OFF:
            if self._power:
                values[self._power["name"]] = False
        else:
            if self._power:
                values[self._power["name"]] = True
            if self._mode and hvac_mode in HA_TO_MODE:
                values[self._mode["name"]] = HA_TO_MODE[hvac_mode]
        if values:
            await self._write(values)

    async def async_turn_on(self) -> None:
        if self._power:
            await self._write({self._power["name"]: True})

    async def async_turn_off(self) -> None:
        if self._power:
            await self._write({self._power["name"]: False})
