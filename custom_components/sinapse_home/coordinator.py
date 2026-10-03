"""Polling coordinator for Sinapse Home."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SinapseApi, SinapseAuthError, SinapseError
from .const import DOMAIN, REFRESH_AFTER_WRITE, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SinapseCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetches all nodes (config + params + connectivity) in one call."""

    def __init__(self, hass: HomeAssistant, api: SinapseApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = api

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            return await self.api.get_nodes()
        except SinapseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SinapseError as err:
            raise UpdateFailed(str(err)) from err

    async def async_set(self, node_id: str, device: str, values: dict[str, Any]) -> None:
        """Write params, update state optimistically and re-poll shortly after."""
        try:
            await self.api.set_params(node_id, {device: values})
        except SinapseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SinapseError as err:
            raise UpdateFailed(str(err)) from err

        node = (self.data or {}).get(node_id)
        if node is not None:
            node.setdefault("params", {}).setdefault(device, {}).update(values)
            self.async_update_listeners()

        async def _refresh(_now) -> None:
            await self.async_request_refresh()

        async_call_later(self.hass, REFRESH_AFTER_WRITE, _refresh)
