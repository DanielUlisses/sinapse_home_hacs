"""Optimistic state must survive cloud polls that still return the old value.

The spa reports changes to the cloud ~20 s after a command, so the re-poll done
right after a write sees stale params. HA must not flip back in the meantime.
"""
import copy
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.sinapse_home.const import DOMAIN

from .conftest import NODE

ENTITY = "switch.sense_duo_hidro_1"


async def _setup(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="a@b.c",
                            data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "x"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _cloud(hidro_on: bool) -> dict:
    node = copy.deepcopy(NODE)
    node["params"]["Hidro 1"]["Status"] = hidro_on
    return {"node1": node}


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_state_kept_while_cloud_is_stale(hass, mock_api, freezer) -> None:
    cloud = {"on": False}
    mock_api["get_nodes"].side_effect = lambda: _cloud(cloud["on"])
    await _setup(hass)

    await hass.services.async_call("switch", "turn_on", {"entity_id": ENTITY}, blocking=True)
    assert hass.states.get(ENTITY).state == "on"

    await _advance(hass, freezer, 4)   # re-poll after write: cloud still says off
    assert hass.states.get(ENTITY).state == "on"

    await _advance(hass, freezer, 30)  # regular poll, cloud still stale
    assert hass.states.get(ENTITY).state == "on"

    cloud["on"] = True                 # device finally reports
    await _advance(hass, freezer, 30)
    assert hass.states.get(ENTITY).state == "on"


async def test_confirmed_value_then_follows_cloud(hass, mock_api, freezer) -> None:
    cloud = {"on": False}
    mock_api["get_nodes"].side_effect = lambda: _cloud(cloud["on"])
    await _setup(hass)

    await hass.services.async_call("switch", "turn_on", {"entity_id": ENTITY}, blocking=True)
    cloud["on"] = True
    await _advance(hass, freezer, 4)   # confirmed -> pending cleared

    cloud["on"] = False                # later turned off on the panel
    await _advance(hass, freezer, 30)
    assert hass.states.get(ENTITY).state == "off"


async def test_unconfirmed_write_expires(hass, mock_api, freezer) -> None:
    mock_api["get_nodes"].side_effect = lambda: _cloud(False)  # command never applied
    await _setup(hass)

    await hass.services.async_call("switch", "turn_on", {"entity_id": ENTITY}, blocking=True)
    for _ in range(4):                 # > 90 s of polls
        await _advance(hass, freezer, 30)
    assert hass.states.get(ENTITY).state == "off"
