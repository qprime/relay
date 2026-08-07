from __future__ import annotations
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from relay.spec.schema import TaskSpec


class CommStrategy(Protocol):
    name: str

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]: ...


class TagStrategy:
    name = "tag"

    def __init__(self, comm_block: dict | None = None) -> None:
        self._comm_block = comm_block or {}

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]:
        issues: list[str] = []
        tags = comm_block.get("tags", [])
        if not isinstance(tags, list):
            return ["Comm.tags must be a list"]
        seen_names: set[str] = set()
        plc_ids = set(spec.plc_ids)
        for i, tag in enumerate(tags):
            if not isinstance(tag, dict):
                issues.append(f"Comm.tags[{i}] must be a mapping")
                continue
            name = tag.get("name")
            if not name or not isinstance(name, str):
                issues.append(f"Comm.tags[{i}].name is required and must be a string")
            elif name in seen_names:
                issues.append(f"Comm.tags[{i}].name {name!r} is duplicated")
            else:
                seen_names.add(name)
            producer = tag.get("produced_by")
            if not producer:
                issues.append(f"Comm.tags[{i}].produced_by is required")
            elif producer not in plc_ids:
                issues.append(
                    f"Comm.tags[{i}].produced_by {producer!r} is not a declared plc_id"
                )
            consumers = tag.get("consumed_by")
            if not isinstance(consumers, list) or not consumers:
                issues.append(f"Comm.tags[{i}].consumed_by must be a non-empty list")
            else:
                for c in consumers:
                    if c not in plc_ids:
                        issues.append(
                            f"Comm.tags[{i}].consumed_by entry {c!r} is not a declared plc_id"
                        )
        return issues


class AddressStrategy:
    name = "address"

    def __init__(self, comm_block: dict | None = None) -> None:
        self._comm_block = comm_block or {}

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]:
        raise NotImplementedError("address-based comm not yet implemented")


_REGISTRY: dict[str, type] = {
    "tag": TagStrategy,
    "address": AddressStrategy,
}


def get_comm_strategy(name: str) -> CommStrategy:
    return build_comm_strategy(name, {})


def build_comm_strategy(name: str, comm_block: dict) -> CommStrategy:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown comm strategy {name!r}; known: {known}")
    return _REGISTRY[name](comm_block)
