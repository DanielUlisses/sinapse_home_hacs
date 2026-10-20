"""Polling coordinator for Sinapse Home: local control first, cloud as fallback."""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SinapseApi, SinapseAuthError, SinapseError
from .const import (
    CLOUD_REFRESH_INTERVAL,
    CONF_NODE_CACHE,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DATA_LOCAL_ADDRESSES,
    DOMAIN,
    LOCAL_DEFAULT_PORT,
    LOCAL_MAX_FAILURES,
    LOCAL_SCAN_INTERVAL,
    LOCAL_TIMEOUT,
    P_LOCAL_CONTROL_POP,
    P_LOCAL_CONTROL_TYPE,
    REFRESH_AFTER_WRITE,
    SCAN_INTERVAL,
    SERVICE_LOCAL_CONTROL,
)
from .local import SinapseLocalClient, http_timeout
from .local_protocol import (
    SinapseLocalAuthError,
    SinapseLocalError,
    SinapseLocalStatusError,
    SinapseLocalUnknownResultError,
)

_LOGGER = logging.getLogger(__name__)


def local_control_service(node: dict[str, Any]) -> tuple[str, str, str] | None:
    """(service name, POP param name, type param name) of the Local Control service."""
    for service in node.get("config", {}).get("services", []):
        if service.get("type") == SERVICE_LOCAL_CONTROL:
            names = {p.get("type"): p.get("name") for p in service.get("params", [])}
            return (
                service.get("name", "Local Control"),
                names.get(P_LOCAL_CONTROL_POP, "POP"),
                names.get(P_LOCAL_CONTROL_TYPE, "Type"),
            )
    return None


def local_credentials(node: dict[str, Any]) -> tuple[str, int] | None:
    """(POP, security type) of the node's Local Control service, if it has one."""
    service = local_control_service(node)
    if service is None:
        return None
    name, pop_name, type_name = service
    values = node.get("params", {}).get(name, {})
    pop = values.get(pop_name)
    sec_type = values.get(type_name, 1)
    if pop is None and sec_type != 0:
        return None
    return str(pop or ""), int(sec_type)


@callback
def async_remember_local_address(
    hass: HomeAssistant, node_id: str, host: str, port: int
) -> None:
    """Record where a node answers local control (from zeroconf discovery)."""
    hass.data.setdefault(DATA_LOCAL_ADDRESSES, {})[node_id] = (host, port)


class SinapseCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Nodes from the cloud (config, POP, connectivity), params from the LAN when possible.

    Every node starts on the cloud. A node whose address is known (zeroconf, the
    manual host option or the last address that worked) is polled locally; while
    that works the coordinator polls every few seconds and only asks the cloud
    for a config refresh now and then. After LOCAL_MAX_FAILURES failed local
    polls the node goes back to the cloud, and local control is retried on every
    following poll.

    The last good node config, Local Control credentials and LAN address are kept
    in the config entry, so Home Assistant can start without the internet.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SinapseApi) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL
        )
        self.api = api
        self._nodes: dict[str, dict[str, Any]] | None = None
        self._from_cache = False
        self._cloud_at = 0.0  # last cloud attempt, successful or not
        self._cloud_failed = False
        self._refresh_credentials = False
        self._cloud_poll_requested = False
        self._clients: dict[str, SinapseLocalClient] = {}
        self._addresses: dict[str, tuple[str, int]] = {}  # last address that worked
        self._failures: dict[str, int] = {}
        self._active: set[str] = set()
        self._shutdown = False

    def connection(self, node_id: str) -> str:
        """How the node is reached right now: "local" or "cloud"."""
        return CONNECTION_LOCAL if node_id in self._active else CONNECTION_CLOUD

    # --- polling -------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        fetched = False
        if self._nodes is None:
            self._nodes = await self._async_first_nodes()
            fetched = True
        local = await self._async_poll_local(self._nodes)
        if not fetched and self._cloud_due(self._nodes):
            try:
                nodes = await self._async_get_cloud_nodes()
            except UpdateFailed as err:
                if not self._active:
                    raise
                _LOGGER.debug("Cloud refresh failed, staying on local control: %s", err)
            else:
                self._nodes = nodes
        if self._from_cache and not self._active:
            raise UpdateFailed("Cloud unreachable and the spa doesn't answer on the LAN")

        for node_id, params in local.items():
            node = self._nodes.get(node_id)
            if node is None:
                continue
            stored = node.setdefault("params", {})
            for device, values in params.items():
                if isinstance(values, dict):
                    stored.setdefault(device, {}).update(values)
                else:
                    stored[device] = values
            node.setdefault("status", {}).setdefault("connectivity", {})["connected"] = True

        self._async_save_cache(self._nodes)
        self.update_interval = LOCAL_SCAN_INTERVAL if self._active else SCAN_INTERVAL
        return self._nodes

    async def _async_first_nodes(self) -> dict[str, dict[str, Any]]:
        """Nodes from the cloud or, when it's unreachable, from the entry's cache."""
        try:
            return await self._async_get_cloud_nodes()
        except UpdateFailed as err:
            nodes = self._cached_nodes()
            if not any(self._local_address(node_id) for node_id in nodes):
                raise
            _LOGGER.info("Cloud unreachable (%s), starting from the saved node config", err)
            self._from_cache = True
            return nodes

    def _cloud_due(self, nodes: dict[str, dict[str, Any]]) -> bool:
        since = time.monotonic() - self._cloud_at
        if self._cloud_failed and self._active and since < SCAN_INTERVAL.total_seconds():
            # Back off after a failed attempt, but only while the LAN supplies data:
            # with no local node, every poll must ask the cloud (or fail).
            return False
        if self._refresh_credentials or self._cloud_poll_requested or self._from_cache:
            return True
        if since >= CLOUD_REFRESH_INTERVAL.total_seconds():
            return True
        if not any(node_id not in self._active for node_id in nodes):
            return False
        # Nodes on the cloud: every poll when nothing is local (the interval is
        # then SCAN_INTERVAL), otherwise at most every SCAN_INTERVAL.
        return not self._active or since >= SCAN_INTERVAL.total_seconds()

    async def _async_get_cloud_nodes(self) -> dict[str, dict[str, Any]]:
        self._cloud_at = time.monotonic()
        try:
            nodes = await self.api.get_nodes()
        except SinapseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SinapseError as err:
            self._cloud_failed = True
            raise UpdateFailed(str(err)) from err
        self._cloud_failed = False
        self._from_cache = False
        self._refresh_credentials = False
        self._cloud_poll_requested = False
        return nodes

    async def _async_poll_local(
        self, nodes: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Read params locally; returns {node_id: params} for the nodes that answered."""
        results: dict[str, dict[str, Any]] = {}
        for node_id, node in nodes.items():
            if self._shutdown:
                break
            client = await self._async_client(node_id, node)
            if client is None:
                continue
            try:
                results[node_id] = await client.async_get_params()
            except SinapseLocalError as err:
                await self._async_local_failed(node_id, client, err)
                continue
            if node_id not in self._active:
                _LOGGER.info("Using local control for %s at %s", node_id, client.host)
            self._active.add(node_id)
            self._failures[node_id] = 0
            self._addresses[node_id] = (client.host, client.port)
        return results

    async def _async_client(
        self, node_id: str, node: dict[str, Any]
    ) -> SinapseLocalClient | None:
        address = self._local_address(node_id)
        credentials = local_credentials(node)
        client = self._clients.get(node_id)
        if client is not None and (
            address is None
            or credentials is None
            or not client.matches(*address, *credentials)
        ):
            await self._async_close_client(node_id, client)
            client = None
        if client is None and address and credentials is not None and not self._shutdown:
            client = self._clients[node_id] = SinapseLocalClient(
                *address, *credentials, node_id=node_id, session_factory=self._http_session
            )
        if client is None:
            self._active.discard(node_id)
        return client

    def _http_session(self) -> aiohttp.ClientSession:
        # Own cookie jar per device: it keys its single session on a cookie.
        # unsafe=True, because aiohttp ignores cookies from IP addresses otherwise.
        return async_create_clientsession(
            self.hass,
            auto_cleanup=False,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=http_timeout(LOCAL_TIMEOUT),
        )

    def _local_address(self, node_id: str) -> tuple[str, int] | None:
        options = self.config_entry.options
        if host := options.get(CONF_HOST):
            return host, int(options.get(CONF_PORT) or LOCAL_DEFAULT_PORT)
        if address := self.hass.data.get(DATA_LOCAL_ADDRESSES, {}).get(node_id):
            return address
        if address := self._addresses.get(node_id):
            return address
        cached = self.config_entry.data.get(CONF_NODE_CACHE, {}).get(node_id, {})
        if cached.get("address"):
            host, port = cached["address"]
            return host, int(port)
        return None

    async def _async_local_failed(
        self, node_id: str, client: SinapseLocalClient, err: SinapseLocalError
    ) -> None:
        if self._clients.get(node_id) is not client:
            return  # a stale client (replaced meanwhile): its failure says nothing
        if isinstance(err, SinapseLocalAuthError):
            self._refresh_credentials = True  # the POP may have changed: ask the cloud
        failures = self._failures[node_id] = self._failures.get(node_id, 0) + 1
        if node_id in self._active and failures < LOCAL_MAX_FAILURES:
            _LOGGER.debug(
                "Local poll of %s failed (%s/%s): %s", node_id, failures, LOCAL_MAX_FAILURES, err
            )
            return
        await self._async_fall_back(node_id, client, err)

    async def _async_fall_back(
        self, node_id: str, client: SinapseLocalClient, err: SinapseLocalError
    ) -> None:
        """Drop the local device and use the cloud (like the app does)."""
        if self._clients.get(node_id) is not client:
            return  # already replaced by a newer client
        if node_id in self._active:
            _LOGGER.info("Local control of %s lost, using the cloud: %s", node_id, err)
        else:
            _LOGGER.debug("Local control of %s unavailable: %s", node_id, err)
        self._active.discard(node_id)
        await self._async_close_client(node_id, client)

    async def _async_close_client(self, node_id: str, client: SinapseLocalClient) -> None:
        """Close a client, and forget it only if it's still the node's current one."""
        if self._clients.get(node_id) is client:
            del self._clients[node_id]
        await client.async_close()

    async def async_shutdown(self) -> None:
        self._shutdown = True
        await super().async_shutdown()
        for node_id, client in list(self._clients.items()):
            await self._async_close_client(node_id, client)

    # --- cache in the config entry --------------------------------------------

    def _cached_nodes(self) -> dict[str, dict[str, Any]]:
        """Nodes rebuilt from the entry cache (config + Local Control values)."""
        cache = self.config_entry.data.get(CONF_NODE_CACHE, {})
        return {
            node_id: {
                "id": node_id,
                "config": copy.deepcopy(dict(cached.get("config", {}))),
                "params": copy.deepcopy(dict(cached.get("params", {}))),
                "status": {"connectivity": {"connected": False}},
            }
            for node_id, cached in cache.items()
        }

    @callback
    def _async_save_cache(self, nodes: dict[str, dict[str, Any]]) -> None:
        """Keep the last good config, credentials and address; write only on change."""
        old = self.config_entry.data.get(CONF_NODE_CACHE, {})
        cache: dict[str, Any] = {}
        for node_id, node in nodes.items():
            service = local_control_service(node)
            values = node.get("params", {}).get(service[0]) if service else None
            address = self._addresses.get(node_id)
            cache[node_id] = {
                "config": node.get("config", {}),
                "params": {service[0]: dict(values)} if service and values else {},
                "address": list(address) if address else old.get(node_id, {}).get("address"),
            }
        if cache != old:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_NODE_CACHE: copy.deepcopy(cache)},
            )

    # --- writes ----------------------------------------------------------------

    async def async_set(self, node_id: str, device: str, values: dict[str, Any]) -> None:
        """Write params (LAN first, cloud when the LAN write provably didn't happen),
        update state optimistically and re-poll shortly after."""
        payload = {device: values}
        if not await self._async_set_local(node_id, payload):
            try:
                await self.api.set_params(node_id, payload)
            except SinapseAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except SinapseError as err:
                raise UpdateFailed(str(err)) from err
            self._cloud_poll_requested = True

        node = (self.data or {}).get(node_id)
        if node is not None:
            node.setdefault("params", {}).setdefault(device, {}).update(values)
            self.async_update_listeners()
        self._async_refresh_later()

    async def _async_set_local(self, node_id: str, payload: dict[str, Any]) -> bool:
        """True if written locally; False if the cloud should be used instead.

        Raises when the device refused the write or may have applied it: sending
        it again (locally or through the cloud) could apply it twice.
        """
        client = self._clients.get(node_id)
        if client is None or node_id not in self._active:
            return False
        try:
            await client.async_set_params(payload)
        except SinapseLocalStatusError as err:
            raise HomeAssistantError(f"The spa refused the command: {err}") from err
        except SinapseLocalUnknownResultError as err:
            await self._async_local_failed(node_id, client, err)
            self._async_refresh_later()
            raise HomeAssistantError(
                "No answer from the spa; the command may or may not have been applied"
            ) from err
        except SinapseLocalError as err:
            if isinstance(err, SinapseLocalAuthError):
                self._refresh_credentials = True
            await self._async_fall_back(node_id, client, err)
            return False
        return True

    @callback
    def _async_refresh_later(self) -> None:
        async def _refresh(_now) -> None:
            await self.async_request_refresh()

        async_call_later(self.hass, REFRESH_AFTER_WRITE, _refresh)
