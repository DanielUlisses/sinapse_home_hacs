"""Switches: Painel, Hidro, Borbulhador (any esp.device.switch)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEV_SWITCH, DOMAIN, P_POWER
from .entity import SinapseEntity, find_param, iter_devices


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SinapseSwitch(coordinator, node_id, device)
        for node_id, device in iter_devices(coordinator)
        if device.get("type") == DEV_SWITCH and find_param(device, P_POWER)
    )


class SinapseSwitch(SinapseEntity, SwitchEntity):
    """On/off device (pump, blower, panel)."""

    def __init__(self, coordinator, node_id, device) -> None:
        super().__init__(coordinator, node_id, device, "power", device["name"])
        self._power = find_param(device, P_POWER)

    @property
    def is_on(self) -> bool | None:
        value = self._value(self._power)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write({self._power["name"]: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write({self._power["name"]: False})
