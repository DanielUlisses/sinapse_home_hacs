"""Shared fixtures."""
import copy
from unittest.mock import AsyncMock, patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

P = lambda name, ptype, dtype, props, **kw: {  # noqa: E731
    "name": name, "type": ptype, "data_type": dtype, "properties": props, **kw
}

NODE = {
    "id": "node1",
    "status": {"connectivity": {"connected": True}},
    "config": {
        "info": {"name": "Sense Duo", "type": "Sinapse", "fw_version": "1.3"},
        "devices": [
            {"name": "Painel", "type": "esp.device.switch", "params": [
                P("ID", "esp.param.name", "string", ["read", "write"]),
                P("Status", "esp.param.power", "bool", ["read", "write"])]},
            {"name": "Aquecedor", "type": "esp.device.thermostat", "params": [
                P("ID", "esp.param.name", "string", ["read", "write"]),
                P("Modo", "esp.param.ac-mode", "string", ["read", "write"]),
                P("Temperatura programada", "esp.param.setpoint-temperature", "int",
                  ["read", "write"], bounds={"max": 40, "min": 20, "step": 1}),
                P("Temperatura", "esp.param.temperature", "float", ["read", "time_series"]),
                P("Status", "esp.ui.toggle", "bool", ["read"]),
                P("Nível", "parametro_nivel_aquecedor", "bool", ["read"]),
                P("Refrigerando", "parametro_refrigerando", "bool", ["read"])]},
            {"name": "Hidro 1", "type": "esp.device.switch", "params": [
                P("ID", "esp.param.name", "string", ["read", "write"]),
                P("Status", "esp.param.power", "bool", ["read", "write"]),
                P("Idioma", "parametro_idioma", "string", ["read", "write"])]},
            {"name": "Borbulhador", "type": "esp.device.switch", "params": [
                P("ID", "esp.param.name", "string", ["read", "write"]),
                P("Status", "esp.param.power", "bool", ["read", "write"])]},
            {"name": "Cromoterapia", "type": "esp.device.light", "params": [
                P("ID", "esp.param.name", "string", ["read", "write"]),
                P("Brilho", "esp.param.brightness", "int", ["read", "write"]),
                P("Saturação", "esp.param.saturation", "int", ["read", "write"]),
                P("Cor", "esp.param.hue", "int", ["read", "write"]),
                P("Status", "esp.param.power", "bool", ["read", "write"]),
                P("Efeitos", "parametro_avanca_cromo", "bool", ["write"])]},
        ],
        "services": [{"name": "Local Control", "type": "esp.service.local_control"}],
    },
    "params": {
        "Aquecedor": {"ID": "Aquecedor SPA", "Modo": "heat", "Nível": True,
                      "Refrigerando": False, "Status": True, "Temperatura": 31.5,
                      "Temperatura programada": 36},
        "Borbulhador": {"ID": "Borbulhador SPA", "Status": False},
        "Cromoterapia": {"Brilho": 100, "Cor": 0, "Efeitos": True,
                         "ID": "Cromoterapia SPA", "Saturação": 100, "Status": False},
        "Hidro 1": {"ID": "Hidro 1 SPA", "Idioma": "pt", "Status": False},
        "Local Control": {"POP": "deadbeef", "Type": 1},
        "Painel": {"ID": "Painel SPA", "Status": True},
    },
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def mock_api():
    with patch(
        "custom_components.sinapse_home.api.SinapseApi.login", new=AsyncMock()
    ) as login, patch(
        "custom_components.sinapse_home.api.SinapseApi.get_nodes",
        new=AsyncMock(side_effect=lambda: {"node1": copy.deepcopy(NODE)}),
    ) as get_nodes, patch(
        "custom_components.sinapse_home.api.SinapseApi.set_params", new=AsyncMock()
    ) as set_params:
        yield {"login": login, "get_nodes": get_nodes, "set_params": set_params}
