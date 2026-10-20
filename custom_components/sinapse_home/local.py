"""Async client for ESP local control (LAN) on a RainMaker node.

Same transport as the Sinapse Home app on the home Wi-Fi: HTTP POSTs to
``/esp_local_ctrl/session`` (handshake) and ``/esp_local_ctrl/control``,
protobuf bodies encrypted with protocomm security 1. RainMaker exposes two
JSON properties, ``config`` and ``params``; ``params`` has the same structure
and write payload as the cloud API.

Errors tell the caller whether a request could have been processed:
- ``SinapseLocalAuthError``: the device rejected the POP; nothing was processed.
- ``SinapseLocalStatusError``: the device processed the request and refused it.
- ``SinapseLocalUnknownResultError``: no usable answer; a write may have been applied.
- any other ``SinapseLocalError``: the request provably wasn't processed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import aiohttp
from yarl import URL

from .const import LOCAL_CONNECT_TIMEOUT, LOCAL_TIMEOUT
from .local_protocol import (
    STATUS_INVALID_SESSION,
    Security0,
    Security1,
    SinapseLocalAuthError,
    SinapseLocalError,
    SinapseLocalStatusError,
    SinapseLocalUnknownResultError,
    decode_property_count,
    decode_property_values,
    decode_set_property_values,
    encode_get_property_count,
    encode_get_property_values,
    encode_set_property_values,
    make_security,
)

_LOGGER = logging.getLogger(__name__)

_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/plain",
    # One TCP connection per request: the session lives in the cookie, and a
    # stale keep-alive socket closed by the device would make a write ambiguous.
    "Connection": "close",
}
PROP_CONFIG = "config"
PROP_PARAMS = "params"

_T = TypeVar("_T")


class _HttpStatusError(SinapseLocalError):
    """The device answered with an HTTP error: protocomm refused the request."""


def http_timeout(total: float) -> aiohttp.ClientTimeout:
    """Timeout for local requests; connect failures stay distinguishable."""
    return aiohttp.ClientTimeout(total=total, sock_connect=min(total, LOCAL_CONNECT_TIMEOUT))


class SinapseLocalClient:
    """One device, one HTTP session (own cookie jar), one security session.

    ``session_factory`` lets Home Assistant supply sessions built on its shared
    connector (``async_create_clientsession``); those are detached, not closed.
    Without it the client creates and closes its own session.
    """

    def __init__(
        self,
        host: str,
        port: int,
        pop: str,
        sec_type: int,
        *,
        node_id: str | None = None,
        session_factory: Callable[[], aiohttp.ClientSession] | None = None,
        timeout: float = LOCAL_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self._pop = pop
        self._sec_type = sec_type
        self._node_id = node_id
        self._session_factory = session_factory
        self._timeout = timeout
        self._http: aiohttp.ClientSession | None = None
        self._security: Security0 | Security1 | None = None
        self._indices: dict[str, int] = {}
        self._closed = False
        # Requests and responses share one CTR stream: never interleave them.
        self._lock = asyncio.Lock()

    def matches(self, host: str, port: int, pop: str, sec_type: int) -> bool:
        """True if this client already talks to that address with those credentials."""
        return (self.host, self.port, self._pop, self._sec_type) == (host, port, pop, sec_type)

    async def async_connect(self) -> None:
        """Establish the security session (handshake) if needed."""

        async def noop() -> None:
            return None

        await self._async_call(noop, idempotent=True)

    async def async_get_params(self) -> dict[str, Any]:
        """Read the ``params`` property: {device: {param: value}}."""

        async def read() -> dict[str, Any]:
            index = self._indices[PROP_PARAMS]
            (prop,) = decode_property_values(
                await self._async_control(encode_get_property_values([index]))
            )
            return _json(prop.value)

        return await self._async_call(read, idempotent=True)

    async def async_set_params(self, payload: dict[str, dict[str, Any]]) -> None:
        """Write params with the cloud payload, e.g. {"Hidro 1": {"Status": True}}.

        Sent again (after a new handshake) only when the first attempt provably
        wasn't processed; see the module docstring for the errors.
        """
        # Raw UTF-8, like the cloud client: RainMaker's JSON parser doesn't
        # unescape \\u sequences in names such as "Saturação".
        value = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

        async def write() -> None:
            index = self._indices[PROP_PARAMS]
            reply = await self._async_control(encode_set_property_values({index: value}))
            try:
                decode_set_property_values(reply)
            except SinapseLocalStatusError:
                raise
            except SinapseLocalError as err:
                # HTTP 200 came back, so the device took the request: whether it
                # applied it is unknown, and the CTR stream may be out of step.
                self._security = None
                raise SinapseLocalUnknownResultError(f"undecodable write reply: {err}") from err

        await self._async_call(write, idempotent=False)

    async def async_close(self) -> None:
        """Close for good; waits for a request in flight (bounded by the timeout)."""
        self._closed = True
        async with self._lock:
            self._security = None
            if self._http is not None:
                if self._session_factory is not None:
                    self._http.detach()  # the connector belongs to Home Assistant
                else:
                    await self._http.close()
                self._http = None

    async def _async_call(
        self, func: Callable[[], Awaitable[_T]], *, idempotent: bool
    ) -> _T:
        """Run func on an established session, re-handshaking once when that is safe."""
        async with self._lock:
            attempt = 0
            while True:
                attempt += 1
                if self._security is None:
                    await self._async_handshake()
                    fresh = True
                else:
                    fresh = False
                try:
                    return await func()
                except SinapseLocalStatusError as err:
                    if err.status != STATUS_INVALID_SESSION or fresh or attempt > 1:
                        raise
                    self._security = None
                except SinapseLocalUnknownResultError:
                    self._security = None
                    if not idempotent or fresh or attempt > 1:
                        raise
                except SinapseLocalError:
                    self._security = None
                    if fresh or attempt > 1:
                        raise
                _LOGGER.debug("Local session to %s lost, re-handshaking", self.host)

    async def _async_handshake(self) -> None:
        """New security session; any failure here means nothing was processed."""
        try:
            await self._async_do_handshake()
        except SinapseLocalAuthError:
            self._security = None
            raise
        except SinapseLocalError as err:
            self._security = None
            raise SinapseLocalError(f"handshake with {self.host} failed: {err}") from err

    async def _async_do_handshake(self) -> None:
        if self._closed:
            raise SinapseLocalError("closed")
        if self._http is None:
            if self._session_factory is not None:
                self._http = self._session_factory()
            else:
                # Own cookie jar: the device keys its single session on a cookie.
                # unsafe=True, because aiohttp ignores cookies from IP addresses otherwise.
                self._http = aiohttp.ClientSession(
                    cookie_jar=aiohttp.CookieJar(unsafe=True),
                    timeout=http_timeout(self._timeout),
                )
        # No cookie on the first request, so the device starts a new session.
        self._http.cookie_jar.clear()
        security = make_security(self._sec_type, self._pop)
        response: bytes | None = None
        step = 0
        while (request := security.next_request(response)) is not None:
            step += 1
            try:
                response = await self._async_post("session", request)
            except SinapseLocalError as err:
                # The device answers a wrong proof (POP) on Command1 with an HTTP
                # error or by dropping the connection once it has the request.
                # Timeouts and connections that never opened say nothing about the POP.
                proof_refused = isinstance(err, _HttpStatusError) or (
                    isinstance(err, SinapseLocalUnknownResultError)
                    and not isinstance(err.__cause__, TimeoutError)
                )
                if step == 2 and isinstance(security, Security1) and proof_refused:
                    raise SinapseLocalAuthError("device rejected the handshake proof") from err
                raise
        self._security = security
        count = decode_property_count(await self._async_control(encode_get_property_count()))
        props = decode_property_values(
            await self._async_control(encode_get_property_values(list(range(count))))
        )
        self._indices = {prop.name: index for index, prop in enumerate(props)}
        if PROP_PARAMS not in self._indices:
            raise SinapseLocalError("device has no params property")
        if self._node_id is not None:
            config = next((p for p in props if p.name == PROP_CONFIG), None)
            node_id = _json(config.value).get("node_id") if config else None
            if node_id != self._node_id:
                raise SinapseLocalError(f"{self.host} answers as another node")
        _LOGGER.debug("Local session established with %s:%s", self.host, self.port)

    async def _async_control(self, message: bytes) -> bytes:
        security = self._security
        if security is None:
            raise SinapseLocalError("session not established")
        try:
            body = await self._async_post("control", security.encrypt(message))
            return security.decrypt(body)
        except BaseException:
            # The shared CTR stream may now be out of step with the device.
            self._security = None
            raise

    async def _async_post(self, endpoint: str, body: bytes) -> bytes:
        if self._closed or self._http is None:
            raise SinapseLocalError("closed")
        url = URL.build(
            scheme="http", host=self.host, port=self.port, path=f"/esp_local_ctrl/{endpoint}"
        )
        try:
            async with self._http.post(url, data=body, headers=_HEADERS) as resp:
                data = await resp.read()
        except (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError) as err:
            # Never connected: nothing was sent.
            raise SinapseLocalError(f"{endpoint}: {err!r}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SinapseLocalUnknownResultError(f"{endpoint}: {err!r}") from err
        if resp.status != 200:
            # protocomm refuses before processing (bad session, failed decryption).
            raise _HttpStatusError(f"{endpoint}: HTTP {resp.status}")
        return data


def _json(value: bytes) -> dict[str, Any]:
    try:
        data = json.loads(value)
    except ValueError as err:
        raise SinapseLocalError("invalid JSON in local property") from err
    if not isinstance(data, dict):
        raise SinapseLocalError("unexpected JSON in local property")
    return data
