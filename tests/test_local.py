"""SinapseLocalClient against a fake device over real HTTP on 127.0.0.1."""
from collections.abc import AsyncGenerator

import pytest

from custom_components.sinapse_home.local import SinapseLocalClient
from custom_components.sinapse_home.local_protocol import (
    SinapseLocalAuthError,
    SinapseLocalError,
    SinapseLocalStatusError,
    SinapseLocalUnknownResultError,
)

from .conftest import NODE, POP
from .fake_device import FakeDevice


@pytest.fixture
async def client(device: FakeDevice) -> AsyncGenerator[SinapseLocalClient]:
    cli = SinapseLocalClient("127.0.0.1", device.port, POP, 1, node_id="node1", timeout=0.3)
    yield cli
    await cli.async_close()


async def test_reads_params_over_encrypted_session(client, device) -> None:
    params = await client.async_get_params()
    assert params == NODE["params"]
    assert device.handshakes == 1
    await client.async_get_params()
    assert device.handshakes == 1  # the session (cookie + CTR stream) is reused


async def test_writes_the_cloud_payload(client, device) -> None:
    payload = {"Cromoterapia": {"Status": True, "Saturação": 80}}
    await client.async_set_params(payload)
    assert device.writes == [payload]
    assert (await client.async_get_params())["Cromoterapia"]["Saturação"] == 80


async def test_wrong_pop_raises_auth_error(device) -> None:
    cli = SinapseLocalClient("127.0.0.1", device.port, "00000000", 1)
    try:
        with pytest.raises(SinapseLocalAuthError):
            await cli.async_get_params()
    finally:
        await cli.async_close()


async def test_lost_session_rehandshakes_once(client, device) -> None:
    await client.async_get_params()
    device.drop_session()  # e.g. the phone app opened its own session
    assert await client.async_get_params() == NODE["params"]
    assert device.handshakes == 2


async def test_unreachable_device_raises(client, device) -> None:
    await client.async_get_params()
    device.available = False
    with pytest.raises(SinapseLocalError):
        await client.async_get_params()
    device.available = True
    assert await client.async_get_params() == NODE["params"]


PAYLOAD = {"Cromoterapia": {"Efeitos": True}}  # not idempotent: advances the effect


async def test_read_timeout_raises(client, device) -> None:
    await client.async_get_params()
    device.hang = {"read"}
    with pytest.raises(SinapseLocalError):
        await client.async_get_params()


async def test_write_lost_after_apply_is_not_resent(client, device) -> None:
    await client.async_get_params()
    device.drop_response_after_apply = True
    with pytest.raises(SinapseLocalUnknownResultError):
        await client.async_set_params(PAYLOAD)
    assert device.writes == [PAYLOAD]


async def test_write_timeout_is_not_resent(client, device) -> None:
    await client.async_get_params()
    device.hang = {"set"}
    with pytest.raises(SinapseLocalUnknownResultError):
        await client.async_set_params(PAYLOAD)
    assert device.handshakes == 1  # no new session, so no second attempt


async def test_write_rejected_before_processing_is_retried_once(client, device) -> None:
    await client.async_get_params()
    device.drop_session()  # the next control request gets HTTP 500, unprocessed
    await client.async_set_params(PAYLOAD)
    assert device.writes == [PAYLOAD]
    assert device.handshakes == 2


async def test_status_error_on_write_keeps_the_session(client, device) -> None:
    await client.async_get_params()
    device.set_statuses = [4]  # InvalidArgument
    with pytest.raises(SinapseLocalStatusError):
        await client.async_set_params(PAYLOAD)
    assert device.writes == []
    await client.async_get_params()
    assert device.handshakes == 1


async def test_invalid_session_status_rehandshakes_and_writes_once(client, device) -> None:
    await client.async_get_params()
    device.set_statuses = [7]  # InvalidSession: not processed
    await client.async_set_params(PAYLOAD)
    assert device.writes == [PAYLOAD]
    assert device.handshakes == 2


async def test_proof_timeout_is_not_an_auth_error(client, device) -> None:
    device.hang = {"session1"}
    with pytest.raises(SinapseLocalError) as err:
        await client.async_get_params()
    assert not isinstance(err.value, SinapseLocalAuthError)


async def test_another_node_at_the_address_is_rejected(device) -> None:
    cli = SinapseLocalClient("127.0.0.1", device.port, POP, 1, node_id="other-node")
    try:
        with pytest.raises(SinapseLocalError) as err:
            await cli.async_get_params()
        assert not isinstance(err.value, SinapseLocalAuthError)
    finally:
        await cli.async_close()


async def test_closed_client_refuses_requests(client, device) -> None:
    await client.async_get_params()
    await client.async_close()
    requests = device.requests
    with pytest.raises(SinapseLocalError):
        await client.async_get_params()
    assert device.requests == requests


async def test_undecodable_write_reply_is_not_resent(client, device) -> None:
    await client.async_get_params()
    device.garble_set_reply = True
    with pytest.raises(SinapseLocalUnknownResultError):
        await client.async_set_params(PAYLOAD)
    assert device.writes == [PAYLOAD]
    assert device.handshakes == 1


async def test_connection_refused_at_the_proof_step_is_not_an_auth_error(client, device) -> None:
    device.refuse_after_session0 = True
    with pytest.raises(SinapseLocalError) as err:
        await client.async_get_params()
    assert not isinstance(err.value, SinapseLocalAuthError)
