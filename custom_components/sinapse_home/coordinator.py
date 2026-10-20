"""Polling coordinator for Sinapse Home."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SinapseApi, SinapseAuthError, SinapseError
from .const import DOMAIN, PENDING_WRITE_TIMEOUT, REFRESH_AFTER_WRITE, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

# (node_id, device, param) -> (written value, expires at)
PendingWrites = dict[tuple[str, str, str], tuple[Any, datetime]]


class SinapseCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetches all nodes (config + params + connectivity) in one call."""

    def __init__(self, hass: HomeAssistant, api: SinapseApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = api
        self._pending: PendingWrites = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            nodes = await self.api.get_nodes()
        except SinapseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SinapseError as err:
            raise UpdateFailed(str(err)) from err
        self._apply_pending(nodes)
        return nodes

    def _apply_pending(self, nodes: dict[str, dict[str, Any]]) -> None:
        """Overlay recent writes the cloud hasn't caught up with yet.

        A write is dropped once the cloud reports the same value (confirmed) or
        when it expires (the device never applied it, so the cloud wins).
        """
        now = dt_util.utcnow()
        for key, (value, expires) in list(self._pending.items()):
            node_id, device, param = key
            params = nodes.get(node_id, {}).setdefault("params", {}).setdefault(device, {})
            if params.get(param) == value or now >= expires:
                del self._pending[key]
            else:
                params[param] = value

    async def async_set(self, node_id: str, device: str, values: dict[str, Any]) -> None:
        """Write params, keep them as pending until the cloud confirms, re-poll soon."""
        try:
            await self.api.set_params(node_id, {device: values})
        except SinapseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SinapseError as err:
            raise UpdateFailed(str(err)) from err

        expires = dt_util.utcnow() + PENDING_WRITE_TIMEOUT
        for param, value in values.items():
            self._pending[(node_id, device, param)] = (value, expires)

        node = (self.data or {}).get(node_id)
        if node is not None:
            node.setdefault("params", {}).setdefault(device, {}).update(values)
            self.async_update_listeners()

        async def _refresh(_now) -> None:
            await self.async_request_refresh()

        async_call_later(self.hass, REFRESH_AFTER_WRITE, _refresh)
