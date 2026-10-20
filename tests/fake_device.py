"""Fake ESP RainMaker node serving esp_local_ctrl over HTTP, for tests.

Mirrors the device side of ESP-IDF: ``protocomm_httpd`` (one session at a time,
tracked by a ``session=<id>`` cookie) and ``security1.c`` (X25519, POP, one
AES-256-CTR stream shared by both directions). Wire helpers are its own, so a
bug in the integration's encoder doesn't cancel out here.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _varint(v: int) -> bytes:
    out = b""
    while v > 0x7F:
        out += bytes([v & 0x7F | 0x80])
        v >>= 7
    return out + bytes([v])


def _field(num: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return _varint(num << 3) + _varint(value)
    return _varint(num << 3 | 2) + _varint(len(value)) + value


def _fields(data: bytes) -> dict[int, list]:
    out: dict[int, list] = {}
    pos = 0
    while pos < len(data):
        key = shift = 0
        while True:
            b = data[pos]; pos += 1
            key |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                break
        val = shift = 0
        while True:
            b = data[pos]; pos += 1
            val |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                break
        if key & 7 == 2:
            val, pos = data[pos : pos + val], pos + val
        out.setdefault(key >> 3, []).append(val)
    return out


class FakeDevice:
    """One node. ``params``/``config`` are what local control reports."""

    def __init__(self, pop: str, params: dict[str, Any], config: dict[str, Any] | None = None) -> None:
        self.pop = pop.encode()
        self.params = copy.deepcopy(params)
        self.config = config or {"node_id": "node1"}
        self.available = True
        # Points that never answer (until stop): "session0", "session1", "read", "set".
        self.hang: set[str] = set()
        self.hung = asyncio.Event()  # set once a request is hanging
        # Apply a write, then drop the connection without answering.
        self.drop_response_after_apply = False
        # Apply a write, then answer HTTP 200 with an undecodable (encrypted) body.
        self.garble_set_reply = False
        # Stop listening right after answering Command0 (new connections refused).
        self.refuse_after_session0 = False
        # Status codes to answer the next writes with (constants.proto Status).
        self.set_statuses: list[int] = []
        self.requests = 0
        self.writes: list[dict[str, Any]] = []
        self.handshakes = 0
        self._release = asyncio.Event()
        self._session_id = 0
        self._cipher = None
        self._established = False
        self._server: TestServer | None = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.port

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/esp_local_ctrl/session", self._session)
        app.router.add_post("/esp_local_ctrl/control", self._control)
        self._server = TestServer(app, host="127.0.0.1")
        await self._server.start_server()

    async def stop(self) -> None:
        self._release.set()
        if self._server:
            await self._server.close()

    async def _maybe_hang(self, point: str) -> bool:
        """Block a request at `point` until stop(); True if it hung."""
        if point not in self.hang:
            return False
        self.hung.set()
        await self._release.wait()
        return True

    def drop_session(self) -> None:
        """Another client (e.g. the phone app) took over the only session."""
        self._session_id += 1000
        self._established = False

    def _same_session(self, request: web.Request) -> bool:
        return request.cookies.get("session") == str(self._session_id)

    async def _session(self, request: web.Request) -> web.Response:
        self.requests += 1
        if not self.available:
            return web.Response(status=503)
        body = await request.read()
        headers = {}
        if not self._same_session(request):
            self._session_id += 1
            self._established = False
            self._cipher = None
            headers["Set-Cookie"] = f"session={self._session_id}"
        sec1 = _fields(_fields(body)[11][0])
        if await self._maybe_hang("session0" if 20 in sec1 else "session1"):
            return web.Response(status=503)
        if 20 in sec1:  # SessionCmd0
            client_pub = _fields(sec1[20][0])[1][0]
            key = X25519PrivateKey.generate()
            self._device_pub = key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            self._client_pub = client_pub
            shared = key.exchange(X25519PublicKey.from_public_bytes(client_pub))
            if self.pop:
                digest = hashlib.sha256(self.pop).digest()
                shared = bytes(a ^ b for a, b in zip(shared, digest))
            rand = os.urandom(16)
            self._cipher = Cipher(algorithms.AES(shared), modes.CTR(rand)).encryptor()
            sr0 = _field(2, self._device_pub) + _field(3, rand)
            resp = _field(2, 1) + _field(11, _field(1, 1) + _field(21, sr0))
            if self.refuse_after_session0:
                for site in self._server.runner.sites:
                    site._server.close()  # asyncio.Server: stop accepting connections
            return web.Response(body=resp, headers=headers)
        # SessionCmd1
        if self._cipher is None:
            return web.Response(status=500)
        verify = _fields(sec1[22][0])[2][0]
        if self._cipher.update(verify) != self._device_pub:
            self._cipher = None  # "Key mismatch. Close connection" -> HTTP 500
            return web.Response(status=500)
        sr1 = _field(3, self._cipher.update(self._client_pub))
        self._established = True
        self.handshakes += 1
        resp = _field(2, 1) + _field(11, _field(1, 3) + _field(23, sr1))
        return web.Response(body=resp, headers=headers)

    async def _control(self, request: web.Request) -> web.Response:
        self.requests += 1
        if not self.available:
            return web.Response(status=503)
        body = await request.read()
        if not self._same_session(request) or not self._established:
            return web.Response(status=500)
        msg = _fields(self._cipher.update(body))
        msg_type = msg.get(1, [0])[0]
        if await self._maybe_hang("set" if msg_type == 6 else "read"):
            return web.Response(status=503)
        props = [("config", self.config), ("params", self.params)]
        if msg_type == 0:  # GetPropertyCount
            out = _field(1, 1) + _field(11, _field(2, len(props)))
        elif msg_type == 4:  # GetPropertyValues
            indices = _fields(msg[12][0]).get(1, [b""])[0]
            infos = b""
            for idx in indices:  # packed uint32, all < 128 here
                name, value = props[idx]
                infos += _field(2, _field(2, name.encode()) + _field(3, 3)
                                + _field(5, json.dumps(value).encode()))
            out = _field(1, 5) + _field(13, infos)
        elif msg_type == 6:  # SetPropertyValues
            status = self.set_statuses.pop(0) if self.set_statuses else 0
            if status == 0:
                for raw in _fields(msg[14][0])[1]:
                    pv = _fields(raw)
                    assert pv[1][0] == 1, "only params is writable"
                    payload = json.loads(pv[2][0])
                    self.writes.append(payload)
                    for device, values in payload.items():
                        self.params.setdefault(device, {}).update(values)
            if self.garble_set_reply and status == 0:
                return web.Response(body=self._cipher.update(b"\xff\xff\xff"))
            if self.drop_response_after_apply:
                request.transport.close()
                raise ConnectionResetError("response dropped after apply")
            out = _field(1, 7) + _field(15, _field(1, status) if status else b"")
        else:
            return web.Response(status=500)
        return web.Response(body=self._cipher.update(out))
