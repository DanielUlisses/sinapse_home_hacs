"""ESP local control protocol: protobuf messages and protocomm security.

Hand-encoded from ESP-IDF (Apache-2.0, Espressif Systems):
- ``components/esp_local_ctrl/proto/esp_local_ctrl.proto``
- ``components/protocomm/proto/{constants,session,sec0,sec1}.proto``
- ``tools/esp_prov/security/security1.py`` (the handshake)

Only the few messages local control needs are encoded, so the integration
doesn't depend on the ``protobuf`` package. This module uses only the standard
library and ``cryptography``, so ``tools/sinapse_local_probe.py`` can load it
outside Home Assistant.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# --- errors ---------------------------------------------------------------


class SinapseLocalError(Exception):
    """Local control failed (transport, protocol or device status)."""


class SinapseLocalAuthError(SinapseLocalError):
    """The device rejected the proof of possession (POP)."""


class SinapseLocalStatusError(SinapseLocalError):
    """The device processed the request and answered with an error status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class SinapseLocalUnknownResultError(SinapseLocalError):
    """The request may have reached the device, but no usable answer came back.

    A write that fails this way may or may not have been applied, so it must not
    be sent again (some params, like the light-effect button, are not idempotent).
    """


# --- protobuf wire format ---------------------------------------------------

_VARINT = 0
_LEN = 2

# constants.proto Status
STATUS_SUCCESS = 0
STATUS_INVALID_SESSION = 7
_STATUS_NAMES = {
    0: "Success",
    1: "InvalidSecScheme",
    2: "InvalidProto",
    3: "TooManySessions",
    4: "InvalidArgument",
    5: "InternalError",
    6: "CryptoError",
    7: "InvalidSession",
}

# esp_local_ctrl.proto LocalCtrlMsgType and LocalCtrlMessage payload fields
_TYPE_CMD_GET_COUNT, _TYPE_RESP_GET_COUNT = 0, 1
_TYPE_CMD_GET_VALS, _TYPE_RESP_GET_VALS = 4, 5
_TYPE_CMD_SET_VALS, _TYPE_RESP_SET_VALS = 6, 7
_PAYLOAD_FIELD = {
    _TYPE_CMD_GET_COUNT: 10,
    _TYPE_RESP_GET_COUNT: 11,
    _TYPE_CMD_GET_VALS: 12,
    _TYPE_RESP_GET_VALS: 13,
    _TYPE_CMD_SET_VALS: 14,
    _TYPE_RESP_SET_VALS: 15,
}


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _key(field: int, wire_type: int) -> bytes:
    return _varint(field << 3 | wire_type)


def _uint(field: int, value: int) -> bytes:
    """Scalar field; proto3 leaves out default (zero) values."""
    return _key(field, _VARINT) + _varint(value) if value else b""


def _bytes(field: int, value: bytes) -> bytes:
    """Bytes/string field; proto3 leaves out empty values."""
    return _key(field, _LEN) + _varint(len(value)) + value if value else b""


def _message(field: int, body: bytes) -> bytes:
    """Sub-message field: always present once set, even when empty."""
    return _key(field, _LEN) + _varint(len(body)) + body


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data) or shift > 63:
            raise SinapseLocalError("truncated or invalid varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _parse(data: bytes) -> dict[int, list[int | bytes]]:
    """Parse one message level into {field: [values]} (varints and bytes only)."""
    fields: dict[int, list[int | bytes]] = {}
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field, wire_type = key >> 3, key & 7
        if wire_type == _VARINT:
            value, pos = _read_varint(data, pos)
        elif wire_type == _LEN:
            length, pos = _read_varint(data, pos)
            if pos + length > len(data):
                raise SinapseLocalError("truncated field")
            value, pos = data[pos : pos + length], pos + length
        elif wire_type == 5:  # fixed32, not used by these messages
            value, pos = data[pos : pos + 4], pos + 4
        elif wire_type == 1:  # fixed64, not used by these messages
            value, pos = data[pos : pos + 8], pos + 8
        else:
            raise SinapseLocalError(f"unsupported wire type {wire_type}")
        if field == 0:
            raise SinapseLocalError("invalid field number 0")
        fields.setdefault(field, []).append(value)
    return fields


def _get_int(fields: dict[int, list[int | bytes]], field: int) -> int:
    value = fields.get(field, [0])[-1]
    if not isinstance(value, int):
        raise SinapseLocalError(f"field {field} is not a varint")
    return value


def _get_bytes(fields: dict[int, list[int | bytes]], field: int) -> bytes:
    value = fields.get(field, [b""])[-1]
    if not isinstance(value, bytes):
        raise SinapseLocalError(f"field {field} is not length-delimited")
    return value


def _check_status(status: int, what: str) -> None:
    if status != STATUS_SUCCESS:
        name = _STATUS_NAMES.get(status, str(status))
        raise SinapseLocalStatusError(f"{what}: device status {name}", status)


# --- esp_local_ctrl messages ------------------------------------------------


@dataclass(frozen=True)
class Property:
    """One esp_local_ctrl property (RainMaker: ``config`` and ``params``)."""

    name: str
    type: int
    flags: int
    value: bytes


def _local_ctrl(msg_type: int, body: bytes) -> bytes:
    return _uint(1, msg_type) + _message(_PAYLOAD_FIELD[msg_type], body)


def _local_ctrl_payload(data: bytes, msg_type: int) -> dict[int, list[int | bytes]]:
    fields = _parse(data)
    if _get_int(fields, 1) != msg_type or _PAYLOAD_FIELD[msg_type] not in fields:
        raise SinapseLocalError("unexpected local control response")
    return _parse(_get_bytes(fields, _PAYLOAD_FIELD[msg_type]))


def encode_get_property_count() -> bytes:
    return _local_ctrl(_TYPE_CMD_GET_COUNT, b"")


def decode_property_count(data: bytes) -> int:
    resp = _local_ctrl_payload(data, _TYPE_RESP_GET_COUNT)
    _check_status(_get_int(resp, 1), "get property count")
    return _get_int(resp, 2)


def encode_get_property_values(indices: list[int]) -> bytes:
    packed = b"".join(_varint(i) for i in indices)
    return _local_ctrl(_TYPE_CMD_GET_VALS, _message(1, packed))


def decode_property_values(data: bytes) -> list[Property]:
    resp = _local_ctrl_payload(data, _TYPE_RESP_GET_VALS)
    _check_status(_get_int(resp, 1), "get property values")
    props = []
    for raw in resp.get(2, []):
        if not isinstance(raw, bytes):
            raise SinapseLocalError("invalid property")
        prop = _parse(raw)
        _check_status(_get_int(prop, 1), "property")
        props.append(
            Property(
                name=_get_bytes(prop, 2).decode(errors="replace"),
                type=_get_int(prop, 3),
                flags=_get_int(prop, 4),
                value=_get_bytes(prop, 5),
            )
        )
    return props


def encode_set_property_values(values: dict[int, bytes]) -> bytes:
    body = b"".join(
        _message(1, _uint(1, index) + _bytes(2, value)) for index, value in values.items()
    )
    return _local_ctrl(_TYPE_CMD_SET_VALS, body)


def decode_set_property_values(data: bytes) -> None:
    resp = _local_ctrl_payload(data, _TYPE_RESP_SET_VALS)
    _check_status(_get_int(resp, 1), "set property values")


# --- protocomm security (session.proto, sec0.proto, sec1.proto) -------------

SEC_SCHEME0 = 0
SEC_SCHEME1 = 1

_SESSION_SEC_VER = 2
_SESSION_SEC0 = 10
_SESSION_SEC1 = 11

# sec1.proto Sec1MsgType and Sec1Payload fields
_SEC1_CMD0, _SEC1_RESP0, _SEC1_CMD1, _SEC1_RESP1 = 0, 1, 2, 3
_SEC1_SC0, _SEC1_SR0, _SEC1_SC1, _SEC1_SR1 = 20, 21, 22, 23


def _session_payload(data: bytes, sec_ver: int, field: int) -> dict[int, list[int | bytes]]:
    fields = _parse(data)
    if _get_int(fields, _SESSION_SEC_VER) != sec_ver or field not in fields:
        raise SinapseLocalError("unexpected security scheme in session response")
    return _parse(_get_bytes(fields, field))


class Security0:
    """protocomm security 0: one round trip, no encryption."""

    def __init__(self) -> None:
        self._step = 0

    def next_request(self, response: bytes | None) -> bytes | None:
        """Return the next session request, or None once the session is up."""
        self._step += 1
        if self._step == 1:
            # SessionData{sec_ver=SecScheme0, sec0{msg=S0_Session_Command, sc{}}}
            return _uint(_SESSION_SEC_VER, SEC_SCHEME0) + _message(
                _SESSION_SEC0, _message(20, b"")
            )
        payload = _session_payload(response or b"", SEC_SCHEME0, _SESSION_SEC0)
        _check_status(_get_int(_parse(_get_bytes(payload, 21)), 1), "session")
        return None

    def encrypt(self, data: bytes) -> bytes:
        return data

    def decrypt(self, data: bytes) -> bytes:
        return data


class Security1:
    """protocomm security 1: X25519 key exchange, POP, AES-256-CTR.

    A single CTR stream serves the whole session, in both directions, exactly
    as on the device: always encrypt a request before decrypting its response.
    """

    def __init__(self, pop: str, private_key: X25519PrivateKey | None = None) -> None:
        self._pop = pop.encode()
        self._key = private_key or X25519PrivateKey.generate()
        self._client_pub = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self._step = 0
        self._cipher = None

    def next_request(self, response: bytes | None) -> bytes | None:
        """Return the next session request, or None once the session is up."""
        self._step += 1
        if self._step == 1:
            return self._sec1(_SEC1_CMD0, _SEC1_SC0, _bytes(1, self._client_pub))
        if self._step == 2:
            resp = self._response(response, _SEC1_SR0)
            device_pub, device_random = _get_bytes(resp, 2), _get_bytes(resp, 3)
            if len(device_pub) != 32 or len(device_random) != 16:
                raise SinapseLocalError("invalid device key or random")
            try:
                shared = self._key.exchange(X25519PublicKey.from_public_bytes(device_pub))
            except ValueError as err:  # e.g. a low-order key giving an all-zero secret
                raise SinapseLocalError("invalid device public key") from err
            if self._pop:
                digest = hashlib.sha256(self._pop).digest()
                shared = bytes(a ^ b for a, b in zip(shared, digest))
            self._cipher = Cipher(algorithms.AES(shared), modes.CTR(device_random)).encryptor()
            verify = self._cipher.update(device_pub)
            return self._sec1(_SEC1_CMD1, _SEC1_SC1, _bytes(2, verify))
        resp = self._response(response, _SEC1_SR1)
        if self.decrypt(_get_bytes(resp, 3)) != self._client_pub:
            raise SinapseLocalAuthError("device rejected the proof of possession")
        return None

    @staticmethod
    def _sec1(msg_type: int, field: int, body: bytes) -> bytes:
        return _uint(_SESSION_SEC_VER, SEC_SCHEME1) + _message(
            _SESSION_SEC1, _uint(1, msg_type) + _message(field, body)
        )

    @staticmethod
    def _response(data: bytes | None, field: int) -> dict[int, list[int | bytes]]:
        payload = _session_payload(data or b"", SEC_SCHEME1, _SESSION_SEC1)
        if field not in payload:
            raise SinapseLocalError("unexpected session response")
        resp = _parse(_get_bytes(payload, field))
        _check_status(_get_int(resp, 1), "session")
        return resp

    def encrypt(self, data: bytes) -> bytes:
        if self._cipher is None:
            raise SinapseLocalError("session not established")
        return self._cipher.update(data)

    decrypt = encrypt  # CTR: same keystream operation, same shared stream


def make_security(sec_type: int, pop: str) -> Security0 | Security1:
    """Security object for the scheme the node reports (``Local Control.Type``)."""
    if sec_type == SEC_SCHEME0:
        return Security0()
    if sec_type == SEC_SCHEME1:
        return Security1(pop)
    raise SinapseLocalError(f"unsupported security scheme {sec_type}")
