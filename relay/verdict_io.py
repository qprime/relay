from __future__ import annotations
import json
import math
from typing import Any, Iterable, TextIO


def _check_str(value: Any, field: str, assertion: Any) -> Any:
    if not isinstance(value, str):
        raise TypeError(
            f"assertion {assertion!r} field {field!r} has unserializable type "
            f"{type(value).__name__}; expected str"
        )
    return value


def _check_bool(value: Any, field: str, assertion: Any) -> Any:
    if not isinstance(value, bool):
        raise TypeError(
            f"assertion {assertion!r} field {field!r} has unserializable type "
            f"{type(value).__name__}; expected bool"
        )
    return value


def _check_ms(value: Any, field: str, assertion: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"assertion {assertion!r} has {field} of type "
            f"{type(value).__name__}; expected a number or None"
        )
    if not math.isfinite(value):
        raise ValueError(
            f"assertion {assertion!r} has {field} of {value}, which JSON "
            "cannot represent portably; milliseconds must be finite"
        )
    return value


def verdict_to_dict(result) -> dict[str, Any]:
    assertion = result.assertion
    return {
        "assertion": _check_str(assertion, "assertion", assertion),
        "passed": _check_bool(result.passed, "passed", assertion),
        "reason": _check_str(result.reason, "reason", assertion),
        "observed_gap_ms": _check_ms(result.observed_gap_ms, "observed_gap_ms", assertion),
        "witness_ms": _check_ms(result.witness_ms, "witness_ms", assertion),
    }


def verdict_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    assertion = data["assertion"]
    return {
        "assertion": _check_str(assertion, "assertion", assertion),
        "passed": _check_bool(data["passed"], "passed", assertion),
        "reason": _check_str(data["reason"], "reason", assertion),
        "observed_gap_ms": _check_ms(data["observed_gap_ms"], "observed_gap_ms", assertion),
        "witness_ms": _check_ms(data["witness_ms"], "witness_ms", assertion),
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
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"results[{index}] has an unreadable field: {exc}"
            ) from exc
    return loaded
