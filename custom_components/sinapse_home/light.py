"""Light for esp.device.light devices (chromotherapy spots)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEV_LIGHT,
    DOMAIN,
    P_BRIGHTNESS,
    P_EFFECT,
    P_HUE,
    P_POWER,
    P_SATURATION,
)
from .entity import SinapseEntity, find_param, iter_devices

# Provisional: which Efeitos value is solid and which blinks is unverified on the device.
# Flip the bools here if the spots behave the other way round.
EFFECTS: dict[str, bool] = {"Solid": False, "Blink": True}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SinapseLight(coordinator, node_id, device)
        for node_id, device in iter_devices(coordinator)
        if device.get("type") == DEV_LIGHT and find_param(device, P_POWER)
    )


class SinapseLight(SinapseEntity, LightEntity):
    """RGB chromotherapy spots."""

    def __init__(self, coordinator, node_id, device) -> None:
        super().__init__(coordinator, node_id, device, "light", device["name"])
        self._power = find_param(device, P_POWER)
        self._brightness = find_param(device, P_BRIGHTNESS)
        self._hue = find_param(device, P_HUE)
        self._sat = find_param(device, P_SATURATION)
        self._effect = find_param(device, P_EFFECT)
        if self._effect:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = list(EFFECTS)
        if self._hue and self._sat:
            mode = ColorMode.HS
        elif self._brightness:
            mode = ColorMode.BRIGHTNESS
        else:
            mode = ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    def is_on(self) -> bool | None:
        value = self._value(self._power)
        return None if value is None else bool(value)

    @property
    def brightness(self) -> int | None:
        value = self._value(self._brightness)
        return None if value is None else round(value * 255 / 100)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        hue, sat = self._value(self._hue), self._value(self._sat)
        if hue is None or sat is None:
            return None
        return float(hue), float(sat)

    @property
    def effect(self) -> str | None:
        value = self._value(self._effect)
        if value is None:
            return None
        return next((name for name, flag in EFFECTS.items() if flag == bool(value)), None)

    async def async_turn_on(self, **kwargs: Any) -> None:
        # Like the app, send only the params whose value actually changes, but always send
        # power: polled state can be stale, and skipping it could leave the light off.
        target: dict[str, Any] = {}
        if ATTR_BRIGHTNESS in kwargs and self._brightness:
            target[self._brightness["name"]] = max(
                1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)
            )
        if ATTR_HS_COLOR in kwargs and self._hue and self._sat:
            hue, sat = kwargs[ATTR_HS_COLOR]
            target[self._hue["name"]] = round(hue)
            target[self._sat["name"]] = round(sat)
        if ATTR_EFFECT in kwargs and self._effect:
            if kwargs[ATTR_EFFECT] not in EFFECTS:
                raise ServiceValidationError(
                    f"Unknown effect {kwargs[ATTR_EFFECT]!r}; expected one of {list(EFFECTS)}"
                )
            target[self._effect["name"]] = EFFECTS[kwargs[ATTR_EFFECT]]
        values = {self._power["name"]: True} | {
            name: value for name, value in target.items() if self._values.get(name) != value
        }
        await self._write(values)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write({self._power["name"]: False})
