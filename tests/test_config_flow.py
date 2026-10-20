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


async def test_options_manual_host(hass: HomeAssistant, mock_api, socket_enabled) -> None:
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, unique_id="a@b.c",
                            data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "x"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    # Nothing listens on 127.0.0.1:9, so the reloaded entry stays on the cloud.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"host": " 127.0.0.1 ", "port": 9})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"host": "127.0.0.1", "port": 9}
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sense_duo_connection").state == "cloud"

    # Clearing the host goes back to zeroconf discovery.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"port": 8080})
    assert entry.options == {}
    await hass.async_block_till_done()
