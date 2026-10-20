"""esp_local_ctrl / protocomm message encoding and the Security1 handshake.

The byte literals were produced with the official ``protobuf`` library from the
descriptors of ESP-IDF's ``esp_local_ctrl.proto``, ``session.proto``,
``sec0.proto`` and ``sec1.proto``, and with a transcription of ESP-IDF's
``esp_prov`` ``security1.py``. They are the independent source of truth.
"""
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from custom_components.sinapse_home.local_protocol import (
    Property,
    Security0,
    Security1,
    SinapseLocalAuthError,
    SinapseLocalError,
    SinapseLocalStatusError,
    decode_property_count,
    decode_property_values,
    decode_set_property_values,
    encode_get_property_count,
    encode_get_property_values,
    encode_set_property_values,
)

GET_COUNT = b"R\x00"
GET_VALS = b"\x08\x04b\x04\n\x02\x00\x01"
SET_VALS = b'\x08\x06r#\n!\x08\x01\x12\x1d{"Hidro 1": {"Status": true}}'
RESP_COUNT = b"\x08\x01Z\x02\x10\x02"
RESP_VALS = (
    b"\x08\x05jG\x12\x1d\x12\x06config\x18\x03 \x01*\x0f{\"node_id\":\"n\"}"
    b"\x12&\x12\x06params\x18\x03*\x1a{\"Painel\":{\"Status\":true}}"
)
RESP_SET_OK = b"\x08\x07z\x00"
RESP_SET_BAD = b"\x08\x07z\x02\x08\x04"  # status InvalidArgument

SEC0_CMD = b"R\x03\xa2\x01\x00"
SEC0_RESP = b"R\x05\x08\x01\xaa\x01\x00"


def test_encode_local_ctrl_commands() -> None:
    assert encode_get_property_count() == GET_COUNT
    assert encode_get_property_values([0, 1]) == GET_VALS
    assert encode_set_property_values({1: b'{"Hidro 1": {"Status": true}}'}) == SET_VALS


def test_decode_local_ctrl_responses() -> None:
    assert decode_property_count(RESP_COUNT) == 2
    assert decode_property_values(RESP_VALS) == [
        Property(name="config", type=3, flags=1, value=b'{"node_id":"n"}'),
        Property(name="params", type=3, flags=0, value=b'{"Painel":{"Status":true}}'),
    ]
    decode_set_property_values(RESP_SET_OK)
    with pytest.raises(SinapseLocalStatusError) as err:
        decode_set_property_values(RESP_SET_BAD)
    assert err.value.status == 4  # InvalidArgument: the device processed and refused it


def test_decode_rejects_wrong_response_type() -> None:
    with pytest.raises(SinapseLocalError):
        decode_property_count(RESP_SET_OK)
    with pytest.raises(SinapseLocalError):
        decode_property_values(b"\xff\xff")  # garbage, e.g. a cipher out of step


# Security1 known-answer vector: client key 0x01*32, device key 0x02*32,
# device random 00..0f, POP "deadbeef".
CLIENT_KEY = X25519PrivateKey.from_private_bytes(bytes([1] * 32))
CLIENT_PUB = bytes.fromhex("a4e09292b651c278b9772c569f5fa9bb13d906b46ab68c9df9dc2b4409f8a209")
DEVICE_PUB = bytes.fromhex("ce8d3ad1ccb633ec7b70c17814a5c76ecd029685050d344745ba05870e587d59")
DEVICE_RANDOM = bytes(range(16))
CLIENT_VERIFY = bytes.fromhex("b4852dc6550a26827bab404f6fbe256b9f452dc8047d104538f2c60d24e45971")
DEVICE_VERIFY = bytes.fromhex("6c82be8bd39b469d9eaee9dc98af7d5fdf186f34326bdb2e35a89899fe781766")

CMD0 = b"\x10\x01Z%\xa2\x01\"\n " + CLIENT_PUB
RESP0 = b"\x10\x01Z9\x08\x01\xaa\x014\x12 " + DEVICE_PUB + b"\x1a\x10" + DEVICE_RANDOM
CMD1 = b"\x10\x01Z'\x08\x02\xb2\x01\"\x12 " + CLIENT_VERIFY
RESP1 = b"\x10\x01Z'\x08\x03\xba\x01\"\x1a " + DEVICE_VERIFY


def test_security1_handshake_known_vector() -> None:
    sec = Security1("deadbeef", private_key=CLIENT_KEY)
    assert sec.next_request(None) == CMD0
    assert sec.next_request(RESP0) == CMD1
    assert sec.next_request(RESP1) is None
    # One CTR stream for the whole session: the first control request
    # continues the keystream after the two 32-byte handshake proofs.
    assert sec.encrypt(GET_COUNT) == bytes.fromhex("ff75")


def test_security1_wrong_pop_is_rejected() -> None:
    sec = Security1("00000000", private_key=CLIENT_KEY)
    sec.next_request(None)
    assert sec.next_request(RESP0) != CMD1
    with pytest.raises(SinapseLocalAuthError):
        sec.next_request(RESP1)


def test_security1_device_error_status() -> None:
    sec = Security1("deadbeef", private_key=CLIENT_KEY)
    sec.next_request(None)
    with pytest.raises(SinapseLocalError):
        # SessionData{sec_ver=1, sec1{msg=Session_Response0, sr0{status=InvalidArgument}}}
        sec.next_request(b"\x10\x01Z\x07\x08\x01\xaa\x01\x02\x08\x04")


def test_security0_handshake() -> None:
    sec = Security0()
    assert sec.next_request(None) == SEC0_CMD
    assert sec.next_request(SEC0_RESP) is None
    assert sec.encrypt(b"abc") == b"abc" and sec.decrypt(b"abc") == b"abc"


def test_security1_low_order_device_key_is_a_local_error() -> None:
    sec = Security1("deadbeef", private_key=CLIENT_KEY)
    sec.next_request(None)
    zero_key_resp0 = b"\x10\x01Z9\x08\x01\xaa\x014\x12 " + bytes(32) + b"\x1a\x10" + DEVICE_RANDOM
    with pytest.raises(SinapseLocalError):  # not a bare ValueError from cryptography
        sec.next_request(zero_key_resp0)
