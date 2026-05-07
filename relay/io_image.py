from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class IOImage:
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.values, MappingProxyType):
            object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @staticmethod
    def empty() -> IOImage:
        return IOImage(values={})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def with_value(self, key: str, value: Any) -> IOImage:
        return IOImage(values={**self.values, key: value})
