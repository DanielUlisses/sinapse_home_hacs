"""Buttons: write-only boolean params (e.g. Cromoterapia 'Efeitos' = next effect)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SinapseEntity
from .generic import is_readable, is_writable, iter_extra_params


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SinapseButton(coordinator, node_id, device, param)
        for node_id, device, param in iter_extra_params(coordinator)
        if param.get("data_type") == "bool" and is_writable(param) and not is_readable(param)
    )


class SinapseButton(SinapseEntity, ButtonEntity):
    """Pulse a write-only bool param."""

    def __init__(self, coordinator, node_id, device, param) -> None:
        super().__init__(
            coordinator, node_id, device, param["name"], f"{device['name']} {param['name']}"
        )
        self._param = param

    async def async_press(self) -> None:
        await self._write({self._param["name"]: True})
