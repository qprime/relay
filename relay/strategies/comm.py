from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


class CommStrategy(Protocol):
    name: str


@dataclass(frozen=True)
class _ModbusTcpStrategy:
    name: str = "modbus_tcp"


_REGISTRY: dict[str, CommStrategy] = {
    "modbus_tcp": _ModbusTcpStrategy(),
}


def get_comm_strategy(name: str) -> CommStrategy:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown comm strategy {name!r}; known: {known}")
    return _REGISTRY[name]
