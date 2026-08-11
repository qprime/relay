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


@dataclass(frozen=True)
class RegisterBinding:
    table: str
    address: int


class CommStrategy(Protocol):
    name: str

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]: ...

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]: ...

    def bindings(self, comm_block: dict) -> dict[str, RegisterBinding]: ...


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

    def bindings(self, comm_block: dict) -> dict[str, RegisterBinding]:
        return {}


_TABLES = ("coil", "discrete_input", "input_register", "holding_register")
_READ_ONLY_TABLES = ("discrete_input", "input_register")
_WRITABLE_BIT_TABLE = "coil"
_ADDRESS_MAX = 65535


class AddressStrategy:
    name = "address"

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]:
        registers = comm_block.get("registers")
        if not isinstance(registers, list) or not registers:
            return ["Comm.registers must be a non-empty list"]
        issues: list[str] = []
        plc_ids = set(spec.plc_ids)
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
            elif table in _READ_ONLY_TABLES:
                issues.append(
                    f"Comm.registers[{i}].table {table!r} is read-only from the "
                    "master's perspective, but every comm signal declares a "
                    "produced_by; field I/O that is genuinely read-only reaches a "
                    f"PLC through Plant.routes, not Comm. Use {_WRITABLE_BIT_TABLE!r}"
                )
                table = None
            elif table != _WRITABLE_BIT_TABLE:
                issues.append(
                    f"Comm.registers[{i}].table {table!r} carries a word, but no "
                    "trigger can emit one: every emit mode assigns a boolean, so "
                    "there is no spec by which a PLC writes a 16-bit value. Use "
                    f"{_WRITABLE_BIT_TABLE!r}"
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
        return issues

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]:
        return _project_entries(comm_block.get("registers"))

    def bindings(self, comm_block: dict) -> dict[str, RegisterBinding]:
        entries = comm_block.get("registers")
        if not isinstance(entries, list):
            return {}
        projected: dict[str, RegisterBinding] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            table = entry.get("table")
            address = entry.get("address")
            if not name or not isinstance(name, str):
                continue
            if not table or not isinstance(table, str):
                continue
            if not isinstance(address, int) or isinstance(address, bool):
                continue
            projected[name] = RegisterBinding(table=table, address=address)
        return projected


_REGISTRY: dict[str, type] = {
    "tag": TagStrategy,
    "address": AddressStrategy,
}


def get_comm_strategy(name: str) -> CommStrategy:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown comm strategy {name!r}; known: {known}")
    return _REGISTRY[name]()


def _resolved(spec: "TaskSpec") -> tuple[CommStrategy, dict] | None:
    block = spec.comm_block
    if not isinstance(block, dict):
        return None
    name = block.get("strategy")
    if not isinstance(name, str) or name not in _REGISTRY:
        return None
    strategy: CommStrategy = _REGISTRY[name]()
    return strategy, block


def comm_signals(spec: "TaskSpec") -> tuple[CommSignal, ...]:
    resolved = _resolved(spec)
    return () if resolved is None else resolved[0].signals(resolved[1])


def comm_bindings(spec: "TaskSpec") -> dict[str, RegisterBinding]:
    resolved = _resolved(spec)
    return {} if resolved is None else resolved[0].bindings(resolved[1])
