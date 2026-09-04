from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

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


@dataclass(frozen=True)
class CanBinding:
    can_id: int


@dataclass(frozen=True)
class CommTransportConfig:
    kind: str
    baud_rate: int | None = None


class CommStrategy(Protocol):
    name: str

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]: ...
    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]: ...
    def bindings(self, comm_block: dict) -> dict[str, RegisterBinding]: ...
    def can_bindings(self, comm_block: dict) -> dict[str, CanBinding]: ...
    def transport_config(self, comm_block: dict) -> CommTransportConfig: ...


def project_entries(entries: object) -> tuple[CommSignal, ...]:
    if not isinstance(entries, list):
        return ()
    projected: list[CommSignal] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        producer = entry.get("produced_by")
        consumers = entry.get("consumed_by")
        if not isinstance(name, str) or not name or not isinstance(producer, str) or not producer:
            continue
        projected.append(
            CommSignal(
                name,
                producer,
                tuple(value for value in consumers if isinstance(value, str))
                if isinstance(consumers, list)
                else (),
            )
        )
    return tuple(projected)


_REGISTRY: dict[str, type[CommStrategy]] = {}
_Strategy = TypeVar("_Strategy", bound=CommStrategy)


def register(strategy: type[_Strategy]) -> type[_Strategy]:
    _REGISTRY[strategy.name] = strategy
    return strategy


def get_comm_strategy(name: str) -> CommStrategy:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown comm strategy {name!r}; known: {known}")
    return _REGISTRY[name]()


def _resolved(spec: "TaskSpec") -> tuple[CommStrategy, dict] | None:
    block = spec.comm_block
    name = block.get("strategy") if isinstance(block, dict) else None
    if not isinstance(name, str) or name not in _REGISTRY:
        return None
    return _REGISTRY[name](), block


def comm_signals(spec: "TaskSpec") -> tuple[CommSignal, ...]:
    resolved = _resolved(spec)
    return () if resolved is None else resolved[0].signals(resolved[1])


def comm_bindings(spec: "TaskSpec") -> dict[str, RegisterBinding]:
    resolved = _resolved(spec)
    return {} if resolved is None else resolved[0].bindings(resolved[1])


def can_bindings(spec: "TaskSpec") -> dict[str, CanBinding]:
    resolved = _resolved(spec)
    return {} if resolved is None else resolved[0].can_bindings(resolved[1])


def comm_transport_config(spec: "TaskSpec") -> CommTransportConfig:
    resolved = _resolved(spec)
    return (
        CommTransportConfig("in_process")
        if resolved is None
        else resolved[0].transport_config(resolved[1])
    )


from relay.strategies.comm import address as _address  # noqa: E402,F401
from relay.strategies.comm import can as _can  # noqa: E402,F401
from relay.strategies.comm import tag as _tag  # noqa: E402,F401
from relay.strategies.comm.address import AddressStrategy  # noqa: E402,F401
from relay.strategies.comm.can import CanStrategy  # noqa: E402,F401
from relay.strategies.comm.tag import TagStrategy  # noqa: E402,F401
