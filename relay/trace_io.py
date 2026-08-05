from __future__ import annotations
import json
import math
from typing import Any, TextIO

from relay.clock import SimClock
from relay.io_image import IOImage
from relay.trace import ScanRecord, TraceLog


ALLOWED_VALUE_TYPES = (bool, int, float)


def _check_values(values: dict[str, Any], where: str) -> dict[str, Any]:
    for key, value in values.items():
        if not isinstance(value, ALLOWED_VALUE_TYPES):
            raise TypeError(
                f"{where} signal {key!r} has unserializable type "
                f"{type(value).__name__}; allowed types are bool, int, float"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(
                f"{where} signal {key!r} is {value}, which JSON cannot represent "
                "portably; signal values must be finite"
            )
    return values


def record_to_dict(record: ScanRecord) -> dict[str, Any]:
    return {
        "plc_id": record.plc_id,
        "tick": record.clock.tick,
        "elapsed_ms": record.clock.elapsed_ms,
        "io_snapshot": _check_values(dict(record.io.values), "io_snapshot"),
        "outputs": _check_values(dict(record.outputs.values), "outputs"),
        "sends": _check_values(dict(record.sends), "sends"),
        "recvs": _check_values(dict(record.recvs), "recvs"),
    }


def record_from_dict(data: dict[str, Any]) -> ScanRecord:
    return ScanRecord(
        plc_id=data["plc_id"],
        clock=SimClock(tick=int(data["tick"]), elapsed_ms=float(data["elapsed_ms"])),
        io=IOImage(values=data["io_snapshot"]),
        outputs=IOImage(values=data["outputs"]),
        sends={k: int(v) for k, v in data["sends"].items()},
        recvs={k: int(v) for k, v in data["recvs"].items()},
    )


def dump_jsonl(trace: TraceLog, stream: TextIO) -> None:
    for record in trace.records:
        stream.write(json.dumps(record_to_dict(record), sort_keys=True) + "\n")


def load_jsonl(stream: TextIO) -> TraceLog:
    trace = TraceLog()
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
            trace.record(record_from_dict(data))
        except KeyError as exc:
            raise KeyError(f"line {lineno} missing required key {exc.args[0]!r}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"line {lineno} has an unreadable field: {exc}") from exc
    return trace
