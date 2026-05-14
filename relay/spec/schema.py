from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from relay.strategies.assertions import parse_assertion
from relay.strategies.comm import get_comm_strategy


@dataclass(frozen=True)
class TaskSpec:
    raw: dict[str, Any]

    @property
    def system_name(self) -> str:
        return self.raw["System"]["name"]

    @property
    def plcs(self) -> list[dict[str, Any]]:
        system = self.raw.get("System") or {}
        return system.get("plcs") or []

    @property
    def plc_ids(self) -> tuple[str, ...]:
        return tuple(p["id"] for p in self.plcs if isinstance(p, dict) and "id" in p)

    @property
    def comm_block(self) -> dict[str, Any]:
        return self.raw.get("Comm", {})

    @property
    def comm_strategy(self) -> str:
        block = self.comm_block
        strategy = block.get("strategy")
        if not strategy:
            raise KeyError("Comm.strategy is not defined")
        return strategy

    @property
    def plant_block(self) -> dict[str, Any]:
        return self.raw.get("Plant", {})

    @property
    def plant_type(self) -> str:
        block = self.plant_block
        plant_type = block.get("type")
        if not plant_type:
            raise KeyError("Plant.type is not defined")
        return plant_type

    @property
    def plant_config(self) -> dict[str, Any]:
        block = self.plant_block
        config = block.get("config")
        if config is None:
            raise KeyError("Plant.config is not defined")
        return config

    @property
    def behavior(self) -> dict[str, Any]:
        return self.raw.get("Behavior", {})

    @property
    def assertions(self) -> list[str]:
        return self.raw.get("Assertions", [])

    @property
    def assertion_signals(self) -> frozenset[str]:
        signals: set[str] = set()
        for a in self.assertions:
            parsed = parse_assertion(a)
            if parsed is None:
                continue
            signals.update(parsed.signals)
        return frozenset(signals)


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
    comm = raw.get("Comm", {})
    strategy = comm.get("strategy") if isinstance(comm, dict) else None
    if not strategy:
        raise ValueError(f"{path}: 'Comm.strategy' is required (e.g. tag)")
    get_comm_strategy(strategy)
