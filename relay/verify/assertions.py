from __future__ import annotations
from dataclasses import dataclass

from relay.strategies.assertions import parse_assertion
from relay.trace import TraceLog


@dataclass(frozen=True)
class AssertionResult:
    assertion: str
    passed: bool
    reason: str


def evaluate_assertion(assertion: str, trace: TraceLog) -> AssertionResult:
    parsed = parse_assertion(assertion)
    if parsed is None:
        return AssertionResult(
            assertion=assertion.strip(),
            passed=False,
            reason=f"unrecognized assertion form: {assertion.strip()}",
        )
    if parsed.form == "EVENTUALLY":
        return _check_eventually(
            assertion.strip(), parsed.signals[0], parsed.within_ms or 0.0, trace
        )
    return _check_precedes(assertion.strip(), parsed.signals[0], parsed.signals[1], trace)


def _signal_value(record, name: str):
    if name in record.outputs.values:
        return record.outputs.values[name]
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
    """PRECEDES is non-strict: `first_ms <= second_ms`.

    Within one scan there is no observable ordering — a tag promotes, the FB
    executes, and outputs fold in a single step sharing one ScanRecord.clock.
    Two signals that become true in the same scan therefore satisfy this
    assertion. It asserts 'not after', not 'strictly before'.

    Bounding the gap between two signals is not expressible in this grammar.
    """
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
