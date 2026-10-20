"""Diagnostics: connection mode is reported, secrets are not."""
import json

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.sinapse_home.const import DOMAIN

from .conftest import POP


async def test_diagnostics_redacts_pop_and_credentials(
    hass: HomeAssistant, hass_client, mock_api
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="a@b.c",
                            data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "s3cret"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    dump = json.dumps(diag)
    assert POP not in dump
    assert "s3cret" not in dump and "a@b.c" not in dump
    node = diag["nodes"]["node1"]
    assert node["connection"] == "cloud"
    assert node["params"]["Local Control"] == "**REDACTED**"
    assert node["params"]["Aquecedor"]["Temperatura"] == 31.5


async def test_diagnostics_redacts_the_local_control_block_by_its_declared_names(
    hass: HomeAssistant, hass_client, mock_api
) -> None:
    import copy

    from .conftest import NODE

    node = copy.deepcopy(NODE)
    node["config"]["services"] = [{
        "name": "Controle", "type": "esp.service.local_control",
        "params": [{"name": "Chave", "type": "esp.param.local_control_pop"},
                   {"name": "Tipo", "type": "esp.param.local_control_type"}],
    }]
    del node["params"]["Local Control"]
    node["params"]["Controle"] = {"Chave": POP, "Tipo": 1}
    mock_api["get_nodes"].side_effect = lambda: {"node1": copy.deepcopy(node)}
    entry = MockConfigEntry(domain=DOMAIN, unique_id="a@b.c",
                            data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "s3cret"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert POP in json.dumps(dict(entry.data))  # cached in the entry for offline starts

    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert POP not in json.dumps(diag)
    assert diag["nodes"]["node1"]["params"]["Controle"] == "**REDACTED**"
