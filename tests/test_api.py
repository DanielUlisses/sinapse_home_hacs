"""API client tests (token renewal)."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sinapse_home.api import SinapseApi
from custom_components.sinapse_home.const import BASE_URL


async def test_login_then_renew_on_401(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    calls = {"nodes": 0}

    aioclient_mock.post(f"{BASE_URL}/login", json={"accesstoken": "tok", "refreshtoken": "rt"})

    async def nodes(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
        calls["nodes"] += 1
        if calls["nodes"] == 1:
            return AiohttpClientMockResponse(method, url, status=401, json={"description": "expired"})
        return AiohttpClientMockResponse(method, url, json={"node_details": [{"id": "n1"}]})

    aioclient_mock.get(f"{BASE_URL}/user/nodes", side_effect=nodes)

    api = SinapseApi(async_get_clientsession(hass), "u", "p")
    nodes_ = await api.get_nodes()
    assert list(nodes_) == ["n1"]
    assert calls["nodes"] == 2
    login_bodies = [c[2] for c in aioclient_mock.mock_calls if str(c[1]).endswith("/login")]
    assert login_bodies[0] == {"user_name": "u", "password": "p"}
    assert login_bodies[1] == {"user_name": "u", "refreshtoken": "rt"}
