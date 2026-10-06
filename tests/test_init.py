"""End-to-end tests with a mocked cloud."""
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sinapse_home.api import SinapseAuthError
from custom_components.sinapse_home.const import DOMAIN


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="a@b.c",
        data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "x"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities_created(hass: HomeAssistant, mock_api) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    ids = sorted(e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
    assert ids == sorted([
        "switch.sense_duo_painel",
        "switch.sense_duo_hidro_1",
        "switch.sense_duo_borbulhador",
        "climate.sense_duo_aquecedor",
        "sensor.sense_duo_aquecedor_temperatura",
        "sensor.sense_duo_aquecedor_temperatura_alvo",
        "binary_sensor.sense_duo_aquecedor_status",
        "binary_sensor.sense_duo_aquecedor_nivel",
        "binary_sensor.sense_duo_aquecedor_refrigerando",
        "light.sense_duo_cromoterapia",
        "button.sense_duo_cromoterapia_efeitos",
    ])

    climate = hass.states.get("climate.sense_duo_aquecedor")
    assert climate.state == "heat"
    assert climate.attributes["current_temperature"] == 31.5
    assert climate.attributes["temperature"] == 36
    assert climate.attributes["hvac_action"] == "heating"
    assert climate.attributes["min_temp"] == 20 and climate.attributes["max_temp"] == 40
    assert hass.states.get("sensor.sense_duo_aquecedor_temperatura").state == "31.5"
    assert hass.states.get("sensor.sense_duo_aquecedor_temperatura_alvo").state == "36"
    assert hass.states.get("switch.sense_duo_painel").state == "on"
    assert hass.states.get("light.sense_duo_cromoterapia").state == "off"


async def test_commands(hass: HomeAssistant, mock_api) -> None:
    await _setup(hass)
    sp = mock_api["set_params"]

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.sense_duo_hidro_1"}, blocking=True)
    sp.assert_awaited_with("node1", {"Hidro 1": {"Status": True}})
    assert hass.states.get("switch.sense_duo_hidro_1").state == "on"  # optimistic

    await hass.services.async_call("climate", "set_temperature",
        {"entity_id": "climate.sense_duo_aquecedor", "temperature": 38}, blocking=True)
    sp.assert_awaited_with("node1", {"Aquecedor": {"Temperatura programada": 38}})
    assert hass.states.get("sensor.sense_duo_aquecedor_temperatura_alvo").state == "38"  # optimistic

    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.sense_duo_cromoterapia", "hs_color": [240, 80], "brightness": 128}, blocking=True)
    sp.assert_awaited_with("node1", {"Cromoterapia": {"Status": True, "Brilho": 50, "Cor": 240, "Saturação": 80}})

    await hass.services.async_call("button", "press",
        {"entity_id": "button.sense_duo_cromoterapia_efeitos"}, blocking=True)
    sp.assert_awaited_with("node1", {"Cromoterapia": {"Efeitos": True}})


async def test_auth_failure_starts_reauth(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = SinapseAuthError("bad")
    entry = MockConfigEntry(domain=DOMAIN, unique_id="a@b.c",
                            data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "x"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())
