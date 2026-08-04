from __future__ import annotations
import json
import math
from typing import Any, Iterable, TextIO


def _check_gap(gap: Any, assertion: str) -> Any:
    if gap is None:
        return None
    if isinstance(gap, bool) or not isinstance(gap, (int, float)):
        raise TypeError(
            f"assertion {assertion!r} has observed_gap_ms of type "
            f"{type(gap).__name__}; expected a number or None"
        )
    if not math.isfinite(gap):
        raise ValueError(
            f"assertion {assertion!r} has observed_gap_ms of {gap}, which JSON "
            "cannot represent portably; gaps must be finite"
        )
    return gap


def verdict_to_dict(result) -> dict[str, Any]:
    return {
        "assertion": result.assertion,
        "passed": bool(result.passed),
        "reason": result.reason,
        "observed_gap_ms": _check_gap(result.observed_gap_ms, result.assertion),
    }


def verdict_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "assertion": data["assertion"],
        "passed": bool(data["passed"]),
        "reason": data["reason"],
        "observed_gap_ms": data["observed_gap_ms"],
    }


def dump_json(results: Iterable[Any], stream: TextIO) -> None:
    entries = [verdict_to_dict(r) for r in results]
    failed = sum(1 for e in entries if not e["passed"])
    document = {
        "results": entries,
        "passed": failed == 0,
        "counts": {
            "total": len(entries),
            "passed": len(entries) - failed,
            "failed": failed,
        },
    }
    stream.write(json.dumps(document, sort_keys=True, indent=2) + "\n")


def load_json(stream: TextIO) -> list[dict[str, Any]]:
    try:
        document = json.loads(stream.read())
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed verdict JSON: {exc.msg}") from exc
    if not isinstance(document, dict):
        raise ValueError(
            f"verdict is a JSON {type(document).__name__}, not an object"
        )
    results = document.get("results")
    if not isinstance(results, list):
        raise ValueError("verdict is missing a 'results' list")
    loaded: list[dict[str, Any]] = []
    for index, entry in enumerate(results):
        if not isinstance(entry, dict):
            raise ValueError(
                f"results[{index}] is a JSON {type(entry).__name__}, not an object"
            )
        try:
            loaded.append(verdict_from_dict(entry))
        except KeyError as exc:
            raise KeyError(
                f"results[{index}] missing required key {exc.args[0]!r}"
            ) from exc
    return loaded
