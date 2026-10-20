"""End-to-end tests with a mocked cloud."""
import copy

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sinapse_home.api import SinapseAuthError
from custom_components.sinapse_home.const import DOMAIN

from .conftest import NODE


def _node(**cromo):
    """NODE with Cromoterapia values overridden."""
    node = copy.deepcopy(NODE)
    node["params"]["Cromoterapia"].update(cromo)
    return node


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


async def test_light_effect_follows_params(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True, Efeitos=True)}
    await _setup(hass)
    light = hass.states.get("light.sense_duo_cromoterapia")
    assert light.attributes["effect_list"] == ["Solid", "Blink"]
    assert light.attributes["effect"] == "Blink"


async def test_light_effect_solid_when_false(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True, Efeitos=False)}
    await _setup(hass)
    assert hass.states.get("light.sense_duo_cromoterapia").attributes["effect"] == "Solid"


async def test_light_hs_color_follows_params(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True, Cor=120, Saturação=60)}
    await _setup(hass)
    assert hass.states.get("light.sense_duo_cromoterapia").attributes["hs_color"] == (120.0, 60.0)


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


async def _light_on(hass: HomeAssistant, mock_api, **data) -> None:
    await hass.services.async_call("light", "turn_on",
        {"entity_id": "light.sense_duo_cromoterapia", **data}, blocking=True)


async def test_light_color_sends_only_hue_like_app(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True, Saturação=100)}
    await _setup(hass)
    await _light_on(hass, mock_api, hs_color=[210, 100])
    mock_api["set_params"].assert_awaited_once_with(
        "node1", {"Cromoterapia": {"Status": True, "Cor": 210}})


async def test_light_plain_turn_on_always_writes_status(hass: HomeAssistant, mock_api) -> None:
    # Polled state may be stale: turn_on must still send Status even if params say on.
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True)}
    await _setup(hass)
    await _light_on(hass, mock_api)
    mock_api["set_params"].assert_awaited_once_with("node1", {"Cromoterapia": {"Status": True}})


async def test_light_effect_writes_bool_when_changed(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True, Efeitos=True)}
    await _setup(hass)
    await _light_on(hass, mock_api, effect="Solid")
    mock_api["set_params"].assert_awaited_once_with(
        "node1", {"Cromoterapia": {"Status": True, "Efeitos": False}})
    assert hass.states.get("light.sense_duo_cromoterapia").attributes["effect"] == "Solid"


async def test_light_effect_unchanged_writes_only_status(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True, Efeitos=True)}
    await _setup(hass)
    await _light_on(hass, mock_api, effect="Blink")
    mock_api["set_params"].assert_awaited_once_with("node1", {"Cromoterapia": {"Status": True}})


async def test_light_effect_while_off_turns_on(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=False, Efeitos=False)}
    await _setup(hass)
    await _light_on(hass, mock_api, effect="Blink")
    mock_api["set_params"].assert_awaited_once_with(
        "node1", {"Cromoterapia": {"Status": True, "Efeitos": True}})


async def test_light_unknown_effect_rejected(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = lambda: {"node1": _node(Status=True)}
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _light_on(hass, mock_api, effect="Rainbow")
    mock_api["set_params"].assert_not_awaited()


async def test_auth_failure_starts_reauth(hass: HomeAssistant, mock_api) -> None:
    mock_api["get_nodes"].side_effect = SinapseAuthError("bad")
    entry = MockConfigEntry(domain=DOMAIN, unique_id="a@b.c",
                            data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "x"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_write_only_bool_button_toggles(hass: HomeAssistant, mock_api) -> None:
    def node():
        n = _node(Efeitos=True)
        cromo = next(d for d in n["config"]["devices"] if d["name"] == "Cromoterapia")
        next(p for p in cromo["params"] if p["name"] == "Efeitos")["type"] = "parametro_outro"
        return {"node1": n}

    mock_api["get_nodes"].side_effect = node
    await _setup(hass)
    await hass.services.async_call("button", "press",
        {"entity_id": "button.sense_duo_cromoterapia_efeitos"}, blocking=True)
    mock_api["set_params"].assert_awaited_with("node1", {"Cromoterapia": {"Efeitos": False}})
    await hass.services.async_call("button", "press",
        {"entity_id": "button.sense_duo_cromoterapia_efeitos"}, blocking=True)
    mock_api["set_params"].assert_awaited_with("node1", {"Cromoterapia": {"Efeitos": True}})
