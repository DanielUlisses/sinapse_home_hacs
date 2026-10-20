"""Diagnostics for Sinapse Home (credentials and the local-control POP redacted)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_NODE_CACHE, DOMAIN
from .coordinator import SinapseCoordinator, local_control_service

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "POP", "pop", "accesstoken", "refreshtoken"}


def _without_local_control(node: dict[str, Any]) -> dict[str, Any]:
    """Node with its whole Local Control params block hidden, whatever its names."""
    service = local_control_service(node)
    params = dict(node.get("params") or {})
    if service and service[0] in params:
        params[service[0]] = REDACTED
    return {**node, "params": params}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: SinapseCoordinator = hass.data[DOMAIN][entry.entry_id]
    nodes = {
        node_id: {
            "connection": coordinator.connection(node_id),
            **_without_local_control(
                {
                    "status": node.get("status"),
                    "config": node.get("config"),
                    "params": node.get("params"),
                }
            ),
        }
        for node_id, node in (coordinator.data or {}).items()
    }
    data = dict(entry.data)
    cache = {
        node_id: _without_local_control(dict(cached))
        for node_id, cached in (data.pop(CONF_NODE_CACHE, None) or {}).items()
    }
    return async_redact_data(
        {
            "entry": {"data": data, "options": dict(entry.options), "node_cache": cache},
            "nodes": nodes,
        },
        TO_REDACT,
    )
