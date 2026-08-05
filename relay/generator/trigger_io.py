from __future__ import annotations
import json
from typing import Any, TextIO

from relay.generator.behavior import EDGES, MODES, Trigger, TriggerEmit, TriggerWhen


def _check_str(value: Any, field: str, trigger_id: Any) -> Any:
    if not isinstance(value, str):
        raise TypeError(
            f"trigger {trigger_id!r} field {field!r} has unserializable type "
            f"{type(value).__name__}; expected str"
        )
    return value


def _check_int(value: Any, field: str, trigger_id: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"trigger {trigger_id!r} field {field!r} has unserializable type "
            f"{type(value).__name__}; expected int"
        )
    return value


def _check_trigger(trigger: Trigger) -> Trigger:
    tid = trigger.id
    _check_str(tid, "id", tid)
    _check_str(trigger.when.signal, "when.signal", tid)
    _check_str(trigger.emit.target, "emit.target", tid)
    _check_int(trigger.when.debounce_ms, "when.debounce_ms", tid)
    if trigger.when.edge not in EDGES:
        raise TypeError(
            f"trigger {tid!r} field 'when.edge' is {trigger.when.edge!r}; "
            f"expected one of {list(EDGES)}"
        )
    if trigger.emit.mode not in MODES:
        raise TypeError(
            f"trigger {tid!r} field 'emit.mode' is {trigger.emit.mode!r}; "
            f"expected one of {list(MODES)}"
        )
    if trigger.emit.mode == "pulse":
        _check_int(trigger.emit.duration_ms, "emit.duration_ms", tid)
    elif trigger.emit.duration_ms is not None:
        raise TypeError(
            f"trigger {tid!r} field 'emit.duration_ms' is "
            f"{trigger.emit.duration_ms!r} for mode {trigger.emit.mode!r}; "
            "expected None for any mode but 'pulse'"
        )
    return trigger


def trigger_to_dict(plc_id: str, trigger: Trigger) -> dict[str, Any]:
    _check_trigger(trigger)
    return {
        "plc_id": plc_id,
        "id": trigger.id,
        "signal": trigger.when.signal,
        "edge": trigger.when.edge,
        "debounce_ms": trigger.when.debounce_ms,
        "target": trigger.emit.target,
        "target_kind": trigger.emit.target_kind,
        "mode": trigger.emit.mode,
        "duration_ms": trigger.emit.duration_ms,
    }


def trigger_from_dict(data: dict[str, Any]) -> tuple[str, Trigger]:
    duration = data["duration_ms"]
    return data["plc_id"], Trigger(
        id=data["id"],
        when=TriggerWhen(
            signal=data["signal"],
            edge=data["edge"],
            debounce_ms=int(data["debounce_ms"]),
        ),
        emit=TriggerEmit(
            target=data["target"],
            target_kind=data["target_kind"],
            mode=data["mode"],
            duration_ms=None if duration is None else int(duration),
        ),
    )


def dump_jsonl(triggers: dict[str, list[Trigger]], stream: TextIO) -> None:
    for plc_id, plc_triggers in triggers.items():
        for trigger in plc_triggers:
            stream.write(json.dumps(trigger_to_dict(plc_id, trigger), sort_keys=True) + "\n")


def load_jsonl(stream: TextIO) -> dict[str, list[Trigger]]:
    triggers: dict[str, list[Trigger]] = {}
    for lineno, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSON on line {lineno}: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"line {lineno} is a JSON {type(data).__name__}, not an object"
            )
        try:
            plc_id, trigger = trigger_from_dict(data)
        except KeyError as exc:
            raise KeyError(f"line {lineno} missing required key {exc.args[0]!r}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"line {lineno} has an unreadable field: {exc}") from exc
        triggers.setdefault(plc_id, []).append(trigger)
    return triggers
