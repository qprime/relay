from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal

from relay.strategies.st_syntax import SCRATCH_PREFIX, SEND_PREFIX


EDGES = ("rising", "falling", "level")
MODES = ("latched", "pulse", "steady")


@dataclass(frozen=True)
class TriggerWhen:
    signal: str
    edge: Literal["rising", "falling", "level"]
    debounce_ms: int


@dataclass(frozen=True)
class TriggerEmit:
    target: str
    target_kind: Literal["tag", "output"]
    mode: Literal["latched", "pulse", "steady"]
    duration_ms: int | None


@dataclass(frozen=True)
class Trigger:
    id: str
    when: TriggerWhen
    emit: TriggerEmit


def parse_triggers(behavior_entry: dict[str, Any]) -> list[Trigger]:
    triggers: list[Trigger] = []
    for raw in behavior_entry.get("triggers") or []:
        when = raw.get("when") or {}
        emit = raw.get("emit") or {}
        target_kind = "tag" if "tag" in emit else "output"
        triggers.append(
            Trigger(
                id=raw.get("id"),
                when=TriggerWhen(
                    signal=when.get("signal"),
                    edge=when.get("edge", "level"),
                    debounce_ms=int(when.get("debounce_ms", 0)),
                ),
                emit=TriggerEmit(
                    target=emit.get(target_kind),
                    target_kind=target_kind,
                    mode=emit.get("mode", "steady"),
                    duration_ms=emit.get("duration_ms"),
                ),
            )
        )
    return triggers
