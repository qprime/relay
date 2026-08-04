from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal

from relay.spec.schema import TaskSpec
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


def compile_plc(triggers: list[Trigger], spec: TaskSpec) -> str:
    consumers = _tag_consumers(spec)
    stanzas = [_compile_trigger(t, consumers) for t in triggers]
    return "\n".join(stanzas)


def _tag_consumers(spec: TaskSpec) -> dict[str, list[str]]:
    consumers: dict[str, list[str]] = {}
    for tag in spec.comm_block.get("tags", []) or []:
        if not isinstance(tag, dict):
            continue
        name = tag.get("name")
        if name:
            consumers[name] = list(tag.get("consumed_by") or [])
    return consumers


def _compile_trigger(trigger: Trigger, consumers: dict[str, list[str]]) -> str:
    lines: list[str] = [f"(* trigger: {trigger.id} *)"]
    source = _emit_debounce(trigger, lines)
    condition = _emit_edge(trigger, source, lines)
    _emit_target(trigger, condition, consumers, lines)
    return "\n".join(lines)


def _emit_debounce(trigger: Trigger, lines: list[str]) -> str:
    if trigger.when.debounce_ms <= 0:
        return trigger.when.signal
    tid = trigger.id
    timer = f"{SCRATCH_PREFIX}debounce_{tid}"
    stable = f"{SCRATCH_PREFIX}stable_{tid}"
    lines.append(f"{timer}(IN := {trigger.when.signal}, PT := T#{trigger.when.debounce_ms}ms);")
    lines.append(f"{stable} := {timer}.Q;")
    return stable


def _emit_edge(trigger: Trigger, source: str, lines: list[str]) -> str:
    edge = trigger.when.edge
    if edge == "level":
        return source
    tid = trigger.id
    detected = f"{SCRATCH_PREFIX}edge_{tid}"
    prev = f"{SCRATCH_PREFIX}prev_{tid}"
    if edge == "rising":
        lines.append(f"{detected} := {source} AND NOT {prev};")
    else:
        lines.append(f"{detected} := NOT {source} AND {prev};")
    lines.append(f"{prev} := {source};")
    return detected


def _emit_target(
    trigger: Trigger, condition: str, consumers: dict[str, list[str]], lines: list[str]
) -> None:
    mode = trigger.emit.mode
    tid = trigger.id

    if mode == "steady":
        value = condition
    elif mode == "latched":
        latched = f"{SCRATCH_PREFIX}latched_{tid}"
        lines.append(f"IF {condition} THEN")
        lines.append(f"{latched} := TRUE;")
        lines.append("END_IF;")
        value = latched
    else:
        pulse = f"{SCRATCH_PREFIX}pulse_{tid}"
        timer = f"{SCRATCH_PREFIX}ton_{tid}"
        lines.append(f"IF {condition} THEN")
        lines.append(f"{pulse} := TRUE;")
        lines.append("END_IF;")
        lines.append(f"{timer}(IN := {pulse}, PT := T#{trigger.emit.duration_ms}ms);")
        lines.append(f"IF {timer}.Q THEN")
        lines.append(f"{pulse} := FALSE;")
        lines.append("END_IF;")
        value = pulse

    for name in _target_names(trigger, consumers):
        lines.append(f"{name} := {value};")


def _target_names(trigger: Trigger, consumers: dict[str, list[str]]) -> list[str]:
    if trigger.emit.target_kind == "output":
        return [trigger.emit.target]
    tag = trigger.emit.target
    return [f"{SEND_PREFIX}{consumer}_{tag}" for consumer in consumers.get(tag, [])]


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
