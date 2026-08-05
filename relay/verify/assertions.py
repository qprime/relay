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
    if parsed.form == "CAUSES":
        return _check_causes(
            assertion.strip(), parsed.signals[0], parsed.signals[1], trace
        )
    if parsed.within_ms is None:
        raise ValueError(f"{parsed.form} parsed without a budget: {assertion!r}")
    if parsed.form == "EVENTUALLY":
        return _check_eventually(
            assertion.strip(), parsed.signals[0], parsed.within_ms, trace
        )
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


def _check_causes(
    assertion: str, cause: str, effect: str, trace: TraceLog
) -> AssertionResult:
    """CAUSES asserts attribution, not timing: `effect`'s first activation must be
    explainable by a message carrying `cause` that the acting PLC actually received.

    The claim is established by matching a per-sender, per-key cumulative send
    count recorded at both ends of the trace, so no clock is read on the pass/fail
    path. That is what makes it survive the move off lockstep simulation: two
    physical PLCs share no scan boundary and no clock, but a counter carried on
    the message is identity that travels with it.

    A receipt only counts as activating if it carried a truthy value. A producer
    that sends every scan — the conveyor's actual shape — delivers `False`
    messages long before the real handoff, and binding to the first receipt of
    any value would attribute the effect to a message that said nothing happened.

    Same-scan receipt and action passes: promotion precedes execution within a
    scan, so a tag that arrives and is acted on in one scan is a causal chain,
    not a coincidence. This mirrors PRECEDES's non-strict same-scan rule.
    """
    acting = next(
        (r for r in trace.records if _signal_value(r, effect)), None
    )
    if acting is None:
        return AssertionResult(
            assertion=assertion,
            passed=False,
            reason=f"signal '{effect}' never became true",
        )

    plc_id = acting.plc_id
    receipts = [
        r
        for r in trace.for_plc(plc_id)
        if cause in r.recvs and _signal_value(r, cause)
    ]
    activating = next(
        (r for r in receipts if r.clock.tick <= acting.clock.tick), None
    )
    if activating is None:
        any_receipt = any(cause in r.recvs for r in trace.for_plc(plc_id))
        if not any_receipt:
            reason = (
                f"'{effect}' became true on '{plc_id}' at tick {acting.clock.tick} "
                f"but '{cause}' was never received there"
            )
        elif not receipts:
            reason = (
                f"'{effect}' became true on '{plc_id}' at tick {acting.clock.tick} "
                f"but every received '{cause}' message carried a false value"
            )
        else:
            reason = (
                f"'{effect}' became true on '{plc_id}' at tick {acting.clock.tick}, "
                f"before the first activating '{cause}' receipt at tick "
                f"{receipts[0].clock.tick}"
            )
        return AssertionResult(assertion=assertion, passed=False, reason=reason)

    seq = activating.recvs[cause]
    sender = next(
        (r for r in trace.records if r.sends.get(cause, 0) >= seq), None
    )
    if sender is None:
        return AssertionResult(
            assertion=assertion,
            passed=False,
            reason=(
                f"'{cause}' receipt seq {seq} on '{plc_id}' has no recorded send; "
                "plant-routed and strategy-routed signals are not attributable"
            ),
        )

    return AssertionResult(
        assertion=assertion,
        passed=True,
        reason=(
            f"'{effect}' true on '{plc_id}' at tick {acting.clock.tick} is caused by "
            f"'{cause}' seq {seq} sent by '{sender.plc_id}' at tick "
            f"{sender.clock.tick} and received at tick {activating.clock.tick}"
        ),
    )


def evaluate_all(assertions: list[str], trace: TraceLog) -> list[AssertionResult]:
    return [evaluate_assertion(a, trace) for a in assertions]
