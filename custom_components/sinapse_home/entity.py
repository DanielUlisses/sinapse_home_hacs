"""Base entity and helpers shared by all platforms."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SinapseCoordinator


def iter_devices(coordinator: SinapseCoordinator) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (node_id, device_config) for every device of every node."""
    for node_id, node in (coordinator.data or {}).items():
        for device in node.get("config", {}).get("devices", []):
            yield node_id, device


def find_param(device: dict[str, Any], param_type: str) -> dict[str, Any] | None:
    """Return the param config of a given RainMaker type, if present."""
    for param in device.get("params", []):
        if param.get("type") == param_type:
            return param
    return None


class SinapseEntity(CoordinatorEntity[SinapseCoordinator]):
    """Entity bound to one RainMaker device inside one node."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SinapseCoordinator,
        node_id: str,
        device: dict[str, Any],
        key: str,
        name: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._node_id = node_id
        self._device = device
        self._dev_name: str = device["name"]
        self._attr_unique_id = f"{node_id}_{self._dev_name}_{key}"
        self._attr_name = name

        info = coordinator.data[node_id].get("config", {}).get("info", {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            name=info.get("name") or "Spa Sinapse",
            manufacturer=MANUFACTURER,
            model=info.get("type"),
            sw_version=info.get("fw_version"),
        )

    @property
    def _node(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._node_id, {})

    @property
    def _values(self) -> dict[str, Any]:
        return self._node.get("params", {}).get(self._dev_name, {})

    def _value(self, param: dict[str, Any] | None) -> Any:
        return None if param is None else self._values.get(param["name"])

    @property
    def available(self) -> bool:
        connected = (
            self._node.get("status", {}).get("connectivity", {}).get("connected", False)
        )
        return super().available and bool(connected)

    async def _write(self, values: dict[str, Any]) -> None:
        await self.coordinator.async_set(self._node_id, self._dev_name, values)
