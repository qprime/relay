from __future__ import annotations
import re
from dataclasses import dataclass

from relay.verify.trace import TraceLog


@dataclass(frozen=True)
class AssertionResult:
    assertion: str
    passed: bool
    reason: str


def evaluate_assertion(assertion: str, trace: TraceLog) -> AssertionResult:
    assertion = assertion.strip()

    eventually_m = re.match(
        r"EVENTUALLY\((\w+),\s*within:\s*(\d+(?:\.\d+)?)ms\)", assertion, re.IGNORECASE
    )
    if eventually_m:
        signal_name = eventually_m.group(1)
        within_ms = float(eventually_m.group(2))
        return _check_eventually(assertion, signal_name, within_ms, trace)

    precedes_m = re.match(r"PRECEDES\((\w+),\s*(\w+)\)", assertion, re.IGNORECASE)
    if precedes_m:
        first_signal = precedes_m.group(1)
        second_signal = precedes_m.group(2)
        return _check_precedes(assertion, first_signal, second_signal, trace)

    return AssertionResult(assertion=assertion, passed=False, reason=f"unrecognized assertion form: {assertion}")


def _signal_value(record, name: str):
    out = record.outputs.get(name)
    if out:
        return out
    return record.io.get(name)


def _check_eventually(
    assertion: str, signal_name: str, within_ms: float, trace: TraceLog
) -> AssertionResult:
    for record in trace.records:
        value = _signal_value(record, signal_name)
        if value and record.clock.elapsed_ms <= within_ms:
            return AssertionResult(assertion=assertion, passed=True, reason=f"signal '{signal_name}' true at {record.clock.elapsed_ms:.1f}ms")
    return AssertionResult(
        assertion=assertion,
        passed=False,
        reason=f"signal '{signal_name}' never true within {within_ms}ms",
    )


def _check_precedes(
    assertion: str, first: str, second: str, trace: TraceLog
) -> AssertionResult:
    first_ms: float | None = None
    second_ms: float | None = None

    for record in trace.records:
        if first_ms is None and _signal_value(record, first):
            first_ms = record.clock.elapsed_ms
        if second_ms is None and _signal_value(record, second):
            second_ms = record.clock.elapsed_ms

    if first_ms is None:
        return AssertionResult(assertion=assertion, passed=False, reason=f"signal '{first}' never became true")
    if second_ms is None:
        return AssertionResult(assertion=assertion, passed=False, reason=f"signal '{second}' never became true")
    if first_ms <= second_ms:
        return AssertionResult(assertion=assertion, passed=True, reason=f"'{first}' at {first_ms:.1f}ms precedes '{second}' at {second_ms:.1f}ms")
    return AssertionResult(
        assertion=assertion,
        passed=False,
        reason=f"'{second}' at {second_ms:.1f}ms preceded '{first}' at {first_ms:.1f}ms",
    )


def evaluate_all(assertions: list[str], trace: TraceLog) -> list[AssertionResult]:
    return [evaluate_assertion(a, trace) for a in assertions]
