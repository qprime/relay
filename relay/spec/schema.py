from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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
    return TaskSpec(raw=raw)
