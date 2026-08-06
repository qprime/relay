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


def _is_comm_tag(name: str, trace: TraceLog) -> bool:
    return any(name in r.sends for r in trace.records)


def _first_true_ms(name: str, trace: TraceLog) -> float | None:
    """When a signal first became true, read on the side that emitted it.

    A comm tag is emitted on its producer and delivered to its consumers, and
    the merged signal view cannot tell those apart: `_signal_value` searches
    every PLC's records, so a tag resolves off whichever PLC happens to appear
    first — the consumer, since the producer never writes a tag to its output
    image. Reading a tag from `sends` anchors it to the emission that the name
    actually denotes, on the PLC that performed it.

    The value is read from the send, not merely counted. A producer that sends
    every scan carries `False` long before the real event, so binding to the
    first send of any value would time the run from a message that said
    nothing happened.
    """
    if _is_comm_tag(name, trace):
        return next(
            (
                r.clock.elapsed_ms
                for r in trace.records
                if name in r.sends and r.sends[name].value
            ),
            None,
        )
    return next(
        (r.clock.elapsed_ms for r in trace.records if _signal_value(r, name)), None
    )


def _check_eventually(
    assertion: str, signal_name: str, within_ms: float, trace: TraceLog
) -> AssertionResult:
    first_ms = _first_true_ms(signal_name, trace)
    if first_ms is not None and first_ms <= within_ms:
        return AssertionResult(assertion=assertion, passed=True, reason=f"signal '{signal_name}' true at {first_ms:.1f}ms")
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

    A comm tag endpoint is read from the producer's `sends`, not from the
    merged signal view — see `_first_true_ms`. Before that rule, a tag resolved
    only through the consumer's I/O image, in the scan where it was promoted
    and acted on, so both endpoints came off one ScanRecord and the form
    compared a clock with itself.

    observed_gap_ms is set on every evaluation where both signals became true,
    pass or fail, so budgets can be derived from measurement rather than guessed.
    """
    first_ms = _first_true_ms(first, trace)
    second_ms = _first_true_ms(second, trace)

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

    The claim rests on message identity recorded where the message was delivered:
    each receipt carries the sender, that sender's per-key sequence number, and
    the value the message actually delivered. No clock is read on the pass/fail
    path, which is what makes the form survive the move off lockstep simulation —
    two physical PLCs share no scan boundary and no clock, but identity carried
    on a message travels with it.

    Every part of the claim is read from the receipt rather than re-derived from
    the trace's merged signal view, because that view cannot answer either
    question. `_signal_value` prefers `outputs` over `io`, so an output sharing
    the tag's name would shadow a `False` delivery and manufacture an activation.
    Sequence numbers are per-sender, so two senders of one key have overlapping
    number spaces and searching for a matching count can land on a PLC that never
    sent to this consumer at all.

    A receipt only activates if the delivered value was truthy. A producer that
    sends every scan — the conveyor's actual shape — delivers `False` long before
    the real handoff, and binding to the first receipt of any value would
    attribute the effect to a message that said nothing happened.

    Plant-routed and strategy-routed messages record no sender, so they are
    unattributable by construction rather than by failing to find a match.

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
    on_plc = trace.for_plc(plc_id)
    receipts = [r for r in on_plc if cause in r.recvs and r.recvs[cause].value]
    activating = next(
        (r for r in receipts if r.clock.tick <= acting.clock.tick), None
    )
    if activating is None:
        if not any(cause in r.recvs for r in on_plc):
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

    receipt = activating.recvs[cause]
    if receipt.sender is None:
        return AssertionResult(
            assertion=assertion,
            passed=False,
            reason=(
                f"'{cause}' received on '{plc_id}' at tick {activating.clock.tick} "
                "records no sender; plant-routed and strategy-routed signals are "
                "not attributable"
            ),
        )

    # Identity is already settled by receipt.sender; this locates which of that
    # sender's scans emitted the seq. `sends` records the scan's high-water count,
    # so a multi-consumer tag emitting two messages of one key in a single scan
    # stores only the last — hence `>=` rather than exact match.
    sender = next(
        (
            r
            for r in trace.for_plc(receipt.sender)
            if cause in r.sends and r.sends[cause].count >= receipt.seq
        ),
        None,
    )
    if sender is None:
        return AssertionResult(
            assertion=assertion,
            passed=False,
            reason=(
                f"'{cause}' receipt seq {receipt.seq} on '{plc_id}' claims sender "
                f"'{receipt.sender}', which recorded no such send"
            ),
        )

    return AssertionResult(
        assertion=assertion,
        passed=True,
        reason=(
            f"'{effect}' true on '{plc_id}' at tick {acting.clock.tick} is caused by "
            f"'{cause}' seq {receipt.seq} sent by '{receipt.sender}' at tick "
            f"{sender.clock.tick} and received at tick {activating.clock.tick}"
        ),
    )


def evaluate_all(assertions: list[str], trace: TraceLog) -> list[AssertionResult]:
    return [evaluate_assertion(a, trace) for a in assertions]
