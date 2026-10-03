"""Minimal async client for the Sinapse Home (ESP RainMaker) cloud API."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import BASE_URL

_LOGGER = logging.getLogger(__name__)


class SinapseError(Exception):
    """Generic API error."""


class SinapseAuthError(SinapseError):
    """Invalid credentials or expired session that could not be renewed."""


class SinapseApi:
    """Talks to /v1 of the RainMaker deployment used by the Sinapse Home app."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        base_url: str = BASE_URL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base = base_url.rstrip("/")
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    async def _post_login(self, body: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.post(
                f"{self._base}/login", json=body, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status in (400, 401, 403):
                    raise SinapseAuthError(data.get("description", "login failed"))
                if resp.status >= 400:
                    raise SinapseError(f"login HTTP {resp.status}: {data}")
                return data
        except aiohttp.ClientError as err:
            raise SinapseError(f"login connection error: {err}") from err

    async def login(self) -> None:
        """Log in with username and password."""
        data = await self._post_login(
            {"user_name": self._username, "password": self._password}
        )
        self._access_token = data["accesstoken"]
        self._refresh_token = data.get("refreshtoken")

    async def _renew(self) -> None:
        """Renew the access token, falling back to a full login."""
        if self._refresh_token:
            try:
                data = await self._post_login(
                    {"user_name": self._username, "refreshtoken": self._refresh_token}
                )
                self._access_token = data["accesstoken"]
                return
            except SinapseError:
                _LOGGER.debug("Refresh token rejected, logging in again")
        await self.login()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json: Any = None,
        _retry: bool = True,
    ) -> dict[str, Any]:
        if not self._access_token:
            await self.login()
        try:
            async with self._session.request(
                method,
                f"{self._base}/{path}",
                params=params,
                json=json,
                headers={"Authorization": self._access_token or ""},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status in (401, 403) and _retry:
                    await self._renew()
                    return await self._request(method, path, params, json, _retry=False)
                data = await resp.json(content_type=None)
                if resp.status in (401, 403):
                    raise SinapseAuthError(str(data))
                if resp.status >= 400:
                    raise SinapseError(f"{method} {path} HTTP {resp.status}: {data}")
                return data or {}
        except aiohttp.ClientError as err:
            raise SinapseError(f"{method} {path} connection error: {err}") from err

    async def get_nodes(self) -> dict[str, dict[str, Any]]:
        """Return {node_id: node_details} with config, params and status."""
        nodes: dict[str, dict[str, Any]] = {}
        start: str | None = None
        while True:
            query = {"node_details": "true"}
            if start:
                query["start_id"] = start
            data = await self._request("GET", "user/nodes", params=query)
            for node in data.get("node_details", []):
                nodes[node["id"]] = node
            start = data.get("next_id")
            if not start:
                return nodes

    async def set_params(self, node_id: str, payload: dict[str, dict[str, Any]]) -> None:
        """Write params, e.g. {"Hidro 1": {"Status": True}}."""
        await self._request(
            "PUT", "user/nodes/params", params={"node_id": node_id}, json=payload
        )
