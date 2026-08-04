from __future__ import annotations
from dataclasses import dataclass

from relay.strategies.assertions import parse_assertion
from relay.trace import TraceLog


@dataclass(frozen=True)
class AssertionResult:
    assertion: str
    passed: bool
    reason: str
    observed_gap_ms: float | None = None


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
    if parsed.within_ms is None:
        raise ValueError(f"PRECEDES parsed without a budget: {assertion!r}")
    return _check_precedes(
        assertion.strip(), parsed.signals[0], parsed.signals[1], parsed.within_ms, trace
    )


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
    assertion: str, first: str, second: str, budget_ms: float, trace: TraceLog
) -> AssertionResult:
    """PRECEDES asserts ordering AND a time budget: `0 <= second_ms - first_ms <= budget_ms`.

    Ordering stays non-strict. Within one scan there is no observable ordering —
    a tag promotes, the FB executes, and outputs fold in a single step sharing
    one ScanRecord.clock. Two signals that become true in the same scan share
    an elapsed_ms, so a strict rule would make the assertion unsatisfiable in
    exactly the case it is most often wanted. It asserts 'not after', not
    'strictly before'.

    The budget is what survives independent clocks. Two physical PLCs have no
    shared scan boundary, so 'same scan' has no referent on hardware while a
    bounded gap does.

    Ordering is checked before budget: a reversed pair reports the ordering
    violation, since reporting a budget overrun for it would mislead.

    observed_gap_ms is set on every evaluation where both signals became true,
    pass or fail, so budgets can be derived from measurement rather than guessed.
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

    gap = second_ms - first_ms
    if gap < 0:
        return AssertionResult(
            assertion=assertion,
            passed=False,
            reason=f"'{second}' at {second_ms:.1f}ms preceded '{first}' at {first_ms:.1f}ms (gap {gap:.1f}ms)",
            observed_gap_ms=gap,
        )
    if gap > budget_ms:
        return AssertionResult(
            assertion=assertion,
            passed=False,
            reason=f"'{first}' at {first_ms:.1f}ms precedes '{second}' at {second_ms:.1f}ms but gap {gap:.1f}ms exceeds budget {budget_ms:.1f}ms",
            observed_gap_ms=gap,
        )
    return AssertionResult(
        assertion=assertion,
        passed=True,
        reason=f"'{first}' at {first_ms:.1f}ms precedes '{second}' at {second_ms:.1f}ms (gap {gap:.1f}ms, budget {budget_ms:.1f}ms)",
        observed_gap_ms=gap,
    )


def evaluate_all(assertions: list[str], trace: TraceLog) -> list[AssertionResult]:
    return [evaluate_assertion(a, trace) for a in assertions]
