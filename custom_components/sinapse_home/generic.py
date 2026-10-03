"""Discovery of params not consumed by switch/light/climate entities."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .const import HANDLED_BY_DEVICE, P_NAME
from .coordinator import SinapseCoordinator
from .entity import iter_devices


def iter_extra_params(
    coordinator: SinapseCoordinator,
) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Yield (node_id, device, param) for params left for generic entities."""
    for node_id, device in iter_devices(coordinator):
        handled = HANDLED_BY_DEVICE.get(device.get("type"), set())
        for param in device.get("params", []):
            ptype = param.get("type")
            if ptype == P_NAME or ptype in handled:
                continue
            yield node_id, device, param


def is_readable(param: dict[str, Any]) -> bool:
    return "read" in param.get("properties", [])


def is_writable(param: dict[str, Any]) -> bool:
    return "write" in param.get("properties", [])
