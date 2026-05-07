from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from relay.strategies.comm import get_comm_strategy


@dataclass(frozen=True)
class TaskSpec:
    raw: dict[str, Any]

    @property
    def system_name(self) -> str:
        return self.raw["System"]["name"]

    @property
    def plcs(self) -> list[dict[str, Any]]:
        return self.raw["System"]["plcs"]

    @property
    def comm(self) -> str:
        return self.raw["System"]["comm"]

    @property
    def plant(self) -> dict[str, Any]:
        return self.raw.get("Plant", {})

    @property
    def behavior(self) -> dict[str, Any]:
        return self.raw.get("Behavior", {})

    @property
    def assertions(self) -> list[str]:
        return self.raw.get("Assertions", [])


def load_spec(path: Path | str) -> TaskSpec:
    text = Path(path).read_text()
    raw = yaml.safe_load(text)
    _validate_required(raw, path)
    return TaskSpec(raw=raw)


def _validate_required(raw: dict[str, Any], path: Path | str) -> None:
    system = raw.get("System")
    if not isinstance(system, dict):
        raise ValueError(f"{path}: top-level 'System' block is required")
    if not system.get("name"):
        raise ValueError(f"{path}: 'System.name' is required")
    plcs = system.get("plcs")
    if not isinstance(plcs, list) or not plcs:
        raise ValueError(f"{path}: 'System.plcs' must be a non-empty list")
    comm = system.get("comm")
    if not comm:
        raise ValueError(f"{path}: 'System.comm' is required (e.g. modbus_tcp)")
    get_comm_strategy(comm)
