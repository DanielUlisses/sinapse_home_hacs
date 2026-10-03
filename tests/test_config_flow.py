"""Config flow tests."""
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.sinapse_home.api import SinapseAuthError
from custom_components.sinapse_home.const import DOMAIN


async def test_user_flow(hass: HomeAssistant, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "Me@Mail.com ", "password": "pw"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"username": "Me@Mail.com", "password": "pw"}
    assert result["result"].unique_id == "me@mail.com"


async def test_invalid_auth(hass: HomeAssistant, mock_api) -> None:
    mock_api["login"].side_effect = SinapseAuthError("nope")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "a@b.c", "password": "bad"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
