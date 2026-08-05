from __future__ import annotations
import json
import math
from typing import Any, TextIO

from relay.clock import SimClock
from relay.io_image import IOImage
from relay.trace import Receipt, ScanRecord, TraceLog


ALLOWED_VALUE_TYPES = (bool, int, float)


def _check_values(values: Any, where: str) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise TypeError(
            f"{where} is a {type(values).__name__}, not an object of signal values"
        )
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
        "recvs": {
            key: _receipt_to_dict(key, receipt)
            for key, receipt in record.recvs.items()
        },
    }


def _receipt_to_dict(key: str, receipt: Receipt) -> dict[str, Any]:
    _check_values({key: receipt.value}, "recvs")
    return {"sender": receipt.sender, "seq": int(receipt.seq), "value": receipt.value}


def _receipt_from_dict(key: str, data: Any) -> Receipt:
    if not isinstance(data, dict):
        raise TypeError(
            f"recvs entry {key!r} is a {type(data).__name__}, not an object with "
            "'sender', 'seq', and 'value'"
        )
    sender = data["sender"]
    if sender is not None and not isinstance(sender, str):
        raise TypeError(
            f"recvs entry {key!r} has sender of type {type(sender).__name__}; "
            "expected a plc_id string or null"
        )
    value = data["value"]
    _check_values({key: value}, "recvs")
    return Receipt(sender=sender, seq=int(data["seq"]), value=value)


def record_from_dict(data: dict[str, Any]) -> ScanRecord:
    return ScanRecord(
        plc_id=data["plc_id"],
        clock=SimClock(tick=int(data["tick"]), elapsed_ms=float(data["elapsed_ms"])),
        io=IOImage(values=_check_values(data["io_snapshot"], "io_snapshot")),
        outputs=IOImage(values=_check_values(data["outputs"], "outputs")),
        sends={k: int(v) for k, v in data["sends"].items()},
        recvs={k: _receipt_from_dict(k, v) for k, v in data["recvs"].items()},
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
