from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from relay.spec.schema import TaskSpec


@dataclass(frozen=True)
class CommSignal:
    name: str
    produced_by: str
    consumed_by: tuple[str, ...]


class CommStrategy(Protocol):
    name: str

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]: ...

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]: ...


def _project_entries(entries: object) -> tuple[CommSignal, ...]:
    if not isinstance(entries, list):
        return ()
    projected: list[CommSignal] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        producer = entry.get("produced_by")
        if not name or not isinstance(name, str):
            continue
        if not producer or not isinstance(producer, str):
            continue
        raw_consumers = entry.get("consumed_by")
        consumers = (
            tuple(c for c in raw_consumers if isinstance(c, str))
            if isinstance(raw_consumers, list)
            else ()
        )
        projected.append(
            CommSignal(name=name, produced_by=producer, consumed_by=consumers)
        )
    return tuple(projected)


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
            elif not isinstance(producer, str) or producer not in plc_ids:
                issues.append(
                    f"Comm.tags[{i}].produced_by {producer!r} is not a declared plc_id"
                )
            consumers = tag.get("consumed_by")
            if not isinstance(consumers, list) or not consumers:
                issues.append(f"Comm.tags[{i}].consumed_by must be a non-empty list")
            else:
                for c in consumers:
                    if not isinstance(c, str) or c not in plc_ids:
                        issues.append(
                            f"Comm.tags[{i}].consumed_by entry {c!r} is not a declared plc_id"
                        )
        return issues

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]:
        return _project_entries(comm_block.get("tags"))


_TABLES = ("coil", "discrete_input", "input_register", "holding_register")
_BIT_TABLES = ("coil", "discrete_input")
_ADDRESS_MAX = 65535


def _emitted_tags(spec: "TaskSpec") -> set[str]:
    emitted: set[str] = set()
    behavior = spec.behavior
    if not isinstance(behavior, dict):
        return emitted
    for entry in behavior.values():
        if not isinstance(entry, dict):
            continue
        triggers = entry.get("triggers")
        if not isinstance(triggers, list):
            continue
        for trigger in triggers:
            if not isinstance(trigger, dict):
                continue
            emit = trigger.get("emit")
            if isinstance(emit, dict) and isinstance(emit.get("tag"), str):
                emitted.add(emit["tag"])
    return emitted


class AddressStrategy:
    name = "address"

    def __init__(self, comm_block: dict | None = None) -> None:
        self._comm_block = comm_block or {}

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]:
        registers = comm_block.get("registers")
        if not isinstance(registers, list) or not registers:
            return ["Comm.registers must be a non-empty list"]
        issues: list[str] = []
        plc_ids = set(spec.plc_ids)
        emitted = _emitted_tags(spec)
        seen_names: set[str] = set()
        seen_bindings: set[tuple[str, int]] = set()
        for i, register in enumerate(registers):
            if not isinstance(register, dict):
                issues.append(f"Comm.registers[{i}] must be a mapping")
                continue
            name = register.get("name")
            if not name or not isinstance(name, str):
                issues.append(
                    f"Comm.registers[{i}].name is required and must be a string"
                )
                name = None
            elif name in seen_names:
                issues.append(f"Comm.registers[{i}].name {name!r} is duplicated")
            else:
                seen_names.add(name)
            producer = register.get("produced_by")
            if not producer:
                issues.append(f"Comm.registers[{i}].produced_by is required")
            elif not isinstance(producer, str) or producer not in plc_ids:
                issues.append(
                    f"Comm.registers[{i}].produced_by {producer!r} is not a declared plc_id"
                )
            consumers = register.get("consumed_by")
            if not isinstance(consumers, list) or not consumers:
                issues.append(
                    f"Comm.registers[{i}].consumed_by must be a non-empty list"
                )
            else:
                for c in consumers:
                    if not isinstance(c, str) or c not in plc_ids:
                        issues.append(
                            f"Comm.registers[{i}].consumed_by entry {c!r} is not a declared plc_id"
                        )
            table = register.get("table")
            if table not in _TABLES:
                issues.append(
                    f"Comm.registers[{i}].table must be one of {list(_TABLES)}, got {table!r}"
                )
                table = None
            address = register.get("address")
            if (
                not isinstance(address, int)
                or isinstance(address, bool)
                or not 0 <= address <= _ADDRESS_MAX
            ):
                issues.append(
                    f"Comm.registers[{i}].address must be an integer in "
                    f"[0, {_ADDRESS_MAX}], got {address!r}"
                )
                address = None
            if table is not None and address is not None:
                binding = (table, address)
                if binding in seen_bindings:
                    issues.append(
                        f"Comm.registers[{i}] binds {table}:{address}, "
                        "which another entry already binds"
                    )
                seen_bindings.add(binding)
            if (
                name is not None
                and table is not None
                and name in emitted
                and table not in _BIT_TABLES
            ):
                issues.append(
                    f"Comm.registers[{i}].table {table!r} is a word table, but "
                    f"{name!r} is a trigger emit target; emitted signals are "
                    "boolean and must bind to 'coil' or 'discrete_input'"
                )
        return issues

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]:
        return _project_entries(comm_block.get("registers"))


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


def comm_signals(spec: "TaskSpec") -> tuple[CommSignal, ...]:
    block = spec.comm_block
    if not isinstance(block, dict):
        return ()
    name = block.get("strategy")
    if not isinstance(name, str) or name not in _REGISTRY:
        return ()
    strategy: CommStrategy = _REGISTRY[name](block)
    return strategy.signals(block)
