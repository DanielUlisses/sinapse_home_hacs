"""Local-first routing with cloud fallback (fake device on 127.0.0.1, mocked cloud)."""
import asyncio
import copy
from datetime import timedelta
from ipaddress import ip_address
import logging
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sinapse_home import coordinator as coordinator_module
from custom_components.sinapse_home.api import SinapseError
from custom_components.sinapse_home.const import (
    CLOUD_REFRESH_INTERVAL,
    DATA_LOCAL_ADDRESSES,
    DOMAIN,
    LOCAL_MAX_FAILURES,
    SCAN_INTERVAL,
)

from .conftest import NODE, POP
from .fake_device import FakeDevice


async def _setup(
    hass: HomeAssistant, device: FakeDevice, manual_host: bool = True
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="a@b.c",
        data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "x"},
        options={CONF_HOST: "127.0.0.1", CONF_PORT: device.port} if manual_host else {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _painel(hass: HomeAssistant) -> str:
    return hass.states.get("switch.sense_duo_painel").state


def _connection(hass: HomeAssistant) -> str:
    return hass.states.get("sensor.sense_duo_connection").state


async def test_local_first_then_cloud_fallback_then_local_again(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    entry = await _setup(hass, device)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    get_nodes = mock_api["get_nodes"]
    assert get_nodes.await_count == 1  # node config and POP come from the cloud

    # Change on the panel: seen through the LAN, without asking the cloud.
    device.params["Painel"]["Status"] = False
    await coordinator.async_refresh()
    assert _painel(hass) == "off"
    assert _connection(hass) == "local"
    assert get_nodes.await_count == 1

    # LAN path lost: after a few failed polls, the cloud takes over.
    device.available = False
    for _ in range(LOCAL_MAX_FAILURES):
        await coordinator.async_refresh()
    assert get_nodes.await_count == 2
    assert _painel(hass) == "on"  # the (mocked) cloud still says on
    assert _connection(hass) == "cloud"

    # LAN back: local resumes on the next poll.
    device.available = True
    await coordinator.async_refresh()
    assert _painel(hass) == "off"
    assert _connection(hass) == "local"


async def test_writes_go_local_first_and_fall_back_with_the_same_payload(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    await _setup(hass, device)
    set_params = mock_api["set_params"]

    async def hidro_on() -> None:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.sense_duo_hidro_1"}, blocking=True
        )

    await hidro_on()
    assert device.writes == [{"Hidro 1": {"Status": True}}]
    set_params.assert_not_awaited()

    device.available = False
    await hidro_on()
    set_params.assert_awaited_once_with("node1", device.writes[0])
    assert hass.states.get("switch.sense_duo_hidro_1").state == "on"


async def test_internet_down_keeps_local_control(
    hass: HomeAssistant, mock_api, device: FakeDevice, freezer
) -> None:
    entry = await _setup(hass, device)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    mock_api["get_nodes"].side_effect = SinapseError("no internet")

    freezer.tick(CLOUD_REFRESH_INTERVAL + timedelta(seconds=1))  # config refresh due
    device.params["Painel"]["Status"] = False
    await coordinator.async_refresh()
    assert mock_api["get_nodes"].await_count == 2
    assert coordinator.last_update_success
    assert _painel(hass) == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.sense_duo_painel"}, blocking=True
    )
    assert device.writes == [{"Painel": {"Status": True}}]
    mock_api["set_params"].assert_not_awaited()


def _zeroconf(port: int, node_id: str = "node1") -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("127.0.0.1"),
        ip_addresses=[ip_address("127.0.0.1")],
        port=port,
        hostname="esp32.local.",
        type="_esp_local_ctrl._tcp.local.",
        name="esp32._esp_local_ctrl._tcp.local.",
        properties={"node_id": node_id},
    )


async def test_zeroconf_discovery_enables_local_control(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    entry = await _setup(hass, device, manual_host=False)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device.params["Painel"]["Status"] = False
    await coordinator.async_refresh()
    assert _painel(hass) == "on"  # address unknown yet: cloud only

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf(device.port)
    )
    assert result["type"] is FlowResultType.ABORT  # no new entry, just the address
    assert result["reason"] == "already_configured"

    await coordinator.async_refresh()
    assert _painel(hass) == "off"


async def test_zeroconf_ignores_other_devices_without_an_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf(8080)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_configured"


async def _discover(hass: HomeAssistant, port: int) -> None:
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf(port)
    )


async def _press_efeitos(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.sense_duo_cromoterapia_efeitos"}, blocking=True
    )


async def test_cold_start_without_internet_uses_cached_config(
    hass: HomeAssistant, mock_api, device: FakeDevice, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    entry = await _setup(hass, device, manual_host=False)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await _discover(hass, device.port)
    await coordinator.async_refresh()
    assert _connection(hass) == "local"
    data = entry.data
    await coordinator.async_refresh()
    assert entry.data is data  # nothing changed: the entry isn't rewritten on every poll

    # Restart with the internet down, before zeroconf has answered again.
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.pop(DATA_LOCAL_ADDRESSES)
    mock_api["get_nodes"].side_effect = SinapseError("no internet")
    device.params["Painel"]["Status"] = False
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _painel(hass) == "off"
    assert _connection(hass) == "local"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.sense_duo_painel"}, blocking=True
    )
    assert device.writes == [{"Painel": {"Status": True}}]
    mock_api["set_params"].assert_not_awaited()
    assert POP not in caplog.text


async def test_cold_start_without_internet_or_address_retries_setup(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    entry = await _setup(hass, device, manual_host=False)  # cloud only: no LAN address yet
    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_api["get_nodes"].side_effect = SinapseError("no internet")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_ambiguous_local_write_is_not_resent(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    await _setup(hass, device)
    device.drop_response_after_apply = True
    with pytest.raises(HomeAssistantError):
        await _press_efeitos(hass)
    assert device.writes == [{"Cromoterapia": {"Efeitos": True}}]  # applied once
    mock_api["set_params"].assert_not_awaited()


async def test_write_refused_by_the_device_is_reported_not_resent(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    await _setup(hass, device)
    device.set_statuses = [4]  # InvalidArgument
    with pytest.raises(HomeAssistantError):
        await _press_efeitos(hass)
    assert device.writes == []
    mock_api["set_params"].assert_not_awaited()
    assert _connection(hass) == "local"


async def test_wrong_pop_counts_toward_fallback_and_refreshes_credentials(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    entry = await _setup(hass, device)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    get_nodes = mock_api["get_nodes"]
    device.pop = b"rotated!"
    device.drop_session()

    await coordinator.async_refresh()
    assert _connection(hass) == "local"  # one failure isn't enough to fall back
    assert get_nodes.await_count == 2  # but the POP is fetched again at once
    for _ in range(LOCAL_MAX_FAILURES - 1):
        await coordinator.async_refresh()
    assert _connection(hass) == "cloud"


def _two_nodes(mock_api) -> None:
    node2 = copy.deepcopy(NODE) | {"id": "node2"}
    mock_api["get_nodes"].side_effect = lambda: {
        "node1": copy.deepcopy(NODE), "node2": copy.deepcopy(node2)
    }


async def test_cloud_only_nodes_follow_the_cloud_cadence(
    hass: HomeAssistant, mock_api, device: FakeDevice, freezer
) -> None:
    _two_nodes(mock_api)
    # The manual host is tried for both nodes; the device answers as node1 only.
    entry = await _setup(hass, device)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    get_nodes = mock_api["get_nodes"]
    assert coordinator.connection("node1") == "local"
    assert coordinator.connection("node2") == "cloud"

    calls = get_nodes.await_count
    await coordinator.async_refresh()  # a local poll, 5 s later in real life
    assert get_nodes.await_count == calls
    freezer.tick(SCAN_INTERVAL)
    await coordinator.async_refresh()
    assert get_nodes.await_count == calls + 1


async def test_unload_during_a_pending_poll_leaves_no_open_session(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    device2 = FakeDevice(POP, NODE["params"], config={"node_id": "node2"})
    await device2.start()
    sessions = []
    create = coordinator_module.async_create_clientsession

    def record(*args, **kwargs):
        sessions.append(create(*args, **kwargs))
        return sessions[-1]

    _two_nodes(mock_api)
    try:
        with patch.object(
            coordinator_module, "async_create_clientsession", record
        ), patch.object(coordinator_module, "LOCAL_TIMEOUT", 0.3):
            entry = await _setup(hass, device, manual_host=False)
            coordinator = hass.data[DOMAIN][entry.entry_id]
            await _discover(hass, device.port)
            await coordinator.async_refresh()
            assert coordinator.connection("node1") == "local"

            # node1 hangs; meanwhile node2 shows up on the LAN and the entry is
            # unloaded. The poll in flight must not open a session for node2.
            device.hang = {"read"}
            poll = hass.async_create_task(coordinator.async_refresh())
            await device.hung.wait()
            await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF},
                data=_zeroconf(device2.port, "node2"),
            )
            assert await hass.config_entries.async_unload(entry.entry_id)
            await poll
            assert device2.handshakes == 0

        assert sessions and all(session.closed for session in sessions)
        requests = device.requests + device2.requests
        await asyncio.sleep(0.4)
        assert device.requests + device2.requests == requests
    finally:
        await device2.stop()


async def test_undecodable_local_write_reply_is_not_resent(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    await _setup(hass, device)
    device.garble_set_reply = True
    with pytest.raises(HomeAssistantError):
        await _press_efeitos(hass)
    assert device.writes == [{"Cromoterapia": {"Efeitos": True}}]
    mock_api["set_params"].assert_not_awaited()


async def test_cloud_only_failure_is_never_reported_as_fresh_data(
    hass: HomeAssistant, mock_api, freezer
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="a@b.c", data={CONF_USERNAME: "a@b.c", CONF_PASSWORD: "x"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    mock_api["get_nodes"].side_effect = SinapseError("cloud down")

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    # HA can fire the next scheduled poll up to ~1 s before the interval elapses.
    freezer.tick(SCAN_INTERVAL - timedelta(seconds=1))
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert mock_api["get_nodes"].await_count == 3


async def test_options_change_reloads_but_saving_the_node_cache_does_not(
    hass: HomeAssistant, mock_api, device: FakeDevice
) -> None:
    entry = await _setup(hass, device, manual_host=False)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await _discover(hass, device.port)
    cache = entry.data.get("node_cache")
    await coordinator.async_refresh()  # local works: the LAN address is saved
    await hass.async_block_till_done()
    assert entry.data["node_cache"] != cache
    assert hass.data[DOMAIN][entry.entry_id] is coordinator  # no reload

    hass.config_entries.async_update_entry(
        entry, options={CONF_HOST: "127.0.0.1", CONF_PORT: device.port}
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][entry.entry_id] is not coordinator  # reloaded
