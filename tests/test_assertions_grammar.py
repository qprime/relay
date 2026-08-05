from __future__ import annotations

import pytest

import relay.verify.assertions as verify_assertions
from relay.clock import SimClock
from relay.io_image import IOImage
from relay.strategies.assertions import ParsedAssertion, parse_assertion
from relay.trace import Receipt, ScanRecord, TraceLog
from relay.verify.assertions import evaluate_assertion


def _trace(*scans: dict[str, bool]) -> TraceLog:
    trace = TraceLog()
    for tick, outputs in enumerate(scans):
        trace.record(
            ScanRecord(
                plc_id="plc_a",
                clock=SimClock(tick=tick, elapsed_ms=tick * 10.0),
                io=IOImage.empty(),
                outputs=IOImage(values=outputs),
            )
        )
    return trace


class TestPrecedesGrammar:
    def test_budgeted_precedes_parses(self):
        parsed = parse_assertion("PRECEDES(a, b, within: 500ms)")
        assert parsed is not None
        assert parsed.form == "PRECEDES"
        assert parsed.signals == ("a", "b")
        assert parsed.within_ms == 500.0

    def test_unbounded_precedes_is_rejected(self):
        assert parse_assertion("PRECEDES(a, b)") is None

    def test_float_budget_parses(self):
        parsed = parse_assertion("PRECEDES(a, b, within: 12.5ms)")
        assert parsed is not None
        assert parsed.within_ms == 12.5

    def test_case_insensitive(self):
        parsed = parse_assertion("precedes(a, b, within: 500ms)")
        assert parsed is not None
        assert parsed.signals == ("a", "b")

    def test_space_before_ms_parses(self):
        precedes = parse_assertion("PRECEDES(a, b, within: 500 ms)")
        assert precedes is not None
        assert precedes.within_ms == 500.0
        eventually = parse_assertion("EVENTUALLY(a, within: 500 ms)")
        assert eventually is not None
        assert eventually.within_ms == 500.0

    def test_negative_budget_is_rejected(self):
        assert parse_assertion("PRECEDES(a, b, within: -5ms)") is None


class TestGrammarAnchoring:
    """Both forms are matched with `fullmatch`, so text outside the call is a
    parse failure rather than silently discarded."""

    @pytest.mark.parametrize(
        "assertion",
        [
            "PRECEDES(a, b, within: 500ms) TRAILING",
            "PRECEDES(a, b, within: 500ms) and then some",
            "EVENTUALLY(a, within: 500ms) GARBAGE",
            "JUNK PRECEDES(a, b, within: 500ms)",
            "JUNK EVENTUALLY(a, within: 500ms)",
            "PRECEDES(a, b, within: 500ms) EVENTUALLY(c, within: 1ms)",
        ],
    )
    def test_text_outside_the_call_is_rejected(self, assertion):
        assert parse_assertion(assertion) is None

    @pytest.mark.parametrize(
        "assertion",
        ["  PRECEDES(a, b, within: 500ms)  ", "\tEVENTUALLY(a, within: 500ms)\n"],
    )
    def test_surrounding_whitespace_is_still_tolerated(self, assertion):
        assert parse_assertion(assertion) is not None


class TestGrammarWhitespaceSymmetry:
    """Task specs are hand-written, so internal padding is tolerated the same
    way in both forms rather than only before `ms`."""

    @pytest.mark.parametrize(
        "assertion, signals",
        [
            ("PRECEDES(a , b, within: 500ms)", ("a", "b")),
            ("PRECEDES(a, b , within: 500ms)", ("a", "b")),
            ("PRECEDES( a, b, within: 500ms )", ("a", "b")),
            ("PRECEDES( a , b , within: 500 ms )", ("a", "b")),
        ],
    )
    def test_precedes_tolerates_internal_padding(self, assertion, signals):
        parsed = parse_assertion(assertion)
        assert parsed is not None
        assert parsed.signals == signals
        assert parsed.within_ms == 500.0

    @pytest.mark.parametrize(
        "assertion",
        [
            "EVENTUALLY(a , within: 500ms)",
            "EVENTUALLY( a, within: 500ms )",
            "EVENTUALLY( a , within: 500 ms )",
        ],
    )
    def test_eventually_tolerates_internal_padding(self, assertion):
        parsed = parse_assertion(assertion)
        assert parsed is not None
        assert parsed.signals == ("a",)
        assert parsed.within_ms == 500.0


class TestPrecedesSemantics:
    def test_same_scan_passes_under_any_budget(self):
        trace = _trace({}, {"a": True, "b": True})
        for budget in ("0ms", "500ms"):
            result = evaluate_assertion(f"PRECEDES(a, b, within: {budget})", trace)
            assert result.passed, result.reason
            assert result.observed_gap_ms == 0.0

    def test_gap_within_budget_passes(self):
        trace = _trace({"a": True}, {"a": True, "b": True})
        result = evaluate_assertion("PRECEDES(a, b, within: 50ms)", trace)
        assert result.passed, result.reason
        assert result.observed_gap_ms == 10.0

    def test_gap_exceeding_budget_fails(self):
        trace = _trace({"a": True}, {"a": True}, {"a": True, "b": True})
        result = evaluate_assertion("PRECEDES(a, b, within: 10ms)", trace)
        assert not result.passed
        assert "gap 20.0ms" in result.reason
        assert "budget 10.0ms" in result.reason

    def test_reversed_order_fails_on_ordering_not_budget(self):
        trace = _trace({"b": True}, {"a": True, "b": True})
        result = evaluate_assertion("PRECEDES(a, b, within: 500ms)", trace)
        assert not result.passed
        assert "preceded" in result.reason
        assert "budget" not in result.reason
        assert result.observed_gap_ms == -10.0

    def test_zero_budget_requires_same_scan(self):
        trace = _trace({"a": True}, {"a": True, "b": True})
        result = evaluate_assertion("PRECEDES(a, b, within: 0ms)", trace)
        assert not result.passed
        assert "gap 10.0ms" in result.reason

    def test_observed_gap_reported_on_pass(self):
        trace = _trace({"a": True}, {"a": True, "b": True})
        result = evaluate_assertion("PRECEDES(a, b, within: 500ms)", trace)
        assert result.passed
        assert result.observed_gap_ms == 10.0

    def test_observed_gap_reported_on_budget_failure(self):
        trace = _trace({"a": True}, {"a": True}, {"a": True, "b": True})
        result = evaluate_assertion("PRECEDES(a, b, within: 10ms)", trace)
        assert not result.passed
        assert result.observed_gap_ms == 20.0

    def test_observed_gap_none_when_signal_never_true(self):
        trace = _trace({"a": True}, {"a": True})
        result = evaluate_assertion("PRECEDES(a, b, within: 500ms)", trace)
        assert not result.passed
        assert result.observed_gap_ms is None


class TestCausesGrammar:
    def test_causes_parses(self):
        parsed = parse_assertion("CAUSES(a, b)")
        assert parsed is not None
        assert parsed.form == "CAUSES"
        assert parsed.signals == ("a", "b")
        assert parsed.within_ms is None

    @pytest.mark.parametrize(
        "assertion",
        ["CAUSES(a, b, within: 500ms)", "CAUSES(a, b, within: 0ms)"],
    )
    def test_budgeted_causes_is_rejected(self, assertion):
        """A budget would reintroduce the clock dependency CAUSES exists to
        escape; time-budget claims are PRECEDES's job."""
        assert parse_assertion(assertion) is None

    @pytest.mark.parametrize(
        "assertion",
        [
            "CAUSES(a, b) TRAILING",
            "JUNK CAUSES(a, b)",
            "CAUSES(a, b) EVENTUALLY(c, within: 1ms)",
            "CAUSES(a)",
        ],
    )
    def test_text_outside_the_call_is_rejected(self, assertion):
        assert parse_assertion(assertion) is None

    @pytest.mark.parametrize(
        "assertion",
        ["  CAUSES(a, b)  ", "\tCAUSES(a, b)\n", "CAUSES( a , b )", "causes(a, b)"],
    )
    def test_padding_and_case_match_existing_forms(self, assertion):
        parsed = parse_assertion(assertion)
        assert parsed is not None
        assert parsed.signals == ("a", "b")


def _causal_trace(*scans) -> TraceLog:
    """Build a multi-PLC trace. Each scan is (plc_id, outputs, sends, recvs),
    where recvs maps signal -> Receipt as `_recv` builds them."""
    trace = TraceLog()
    ticks: dict[str, int] = {}
    for plc_id, outputs, sends, recvs in scans:
        tick = ticks.get(plc_id, 0)
        ticks[plc_id] = tick + 1
        trace.record(
            ScanRecord(
                plc_id=plc_id,
                clock=SimClock(tick=tick, elapsed_ms=tick * 10.0),
                io=IOImage(values={k: r.value for k, r in recvs.items()}),
                outputs=IOImage(values=outputs),
                sends=sends,
                recvs=recvs,
            )
        )
    return trace


def _recv(**receipts):
    """signal=(sender, seq, value) -> {signal: Receipt}. sender None models a
    plant-routed or strategy-routed message, which carries no PLC sender."""
    return {
        key: Receipt(sender=sender, seq=seq, value=value)
        for key, (sender, seq, value) in receipts.items()
    }


class TestCausesSemantics:
    def test_same_scan_receipt_and_action_passes(self):
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 1, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert result.passed, result.reason
        assert "seq 1" in result.reason

    def test_later_scan_action_passes(self):
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {}, {}, _recv(tag=("plc_a", 1, True))),
            ("plc_a", {}, {}, {}),
            ("plc_b", {"effect": True}, {}, {}),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert result.passed, result.reason

    def test_effect_never_true_fails(self):
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": False}, {}, _recv(tag=("plc_a", 1, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed
        assert "never became true" in result.reason

    def test_effect_true_before_activating_receipt_fails(self):
        """An evaluator that ignores per-PLC tick ordering — matching any receipt
        anywhere in the trace — passes this. It must fail: the effect fired
        before any message could have caused it."""
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": True}, {}, {}),
            ("plc_a", {}, {"tag": 2}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 2, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed
        assert "before the first activating" in result.reason

    def test_false_receipts_do_not_activate(self):
        """The conveyor's actual shape: the producer sends every scan, carrying
        False until the real event. Binding to the first receipt of any value
        would attribute the effect to a message that said nothing happened."""
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {}, {}, _recv(tag=("plc_a", 1, False))),
            ("plc_a", {}, {"tag": 2}, {}),
            ("plc_b", {}, {}, _recv(tag=("plc_a", 2, False))),
            ("plc_a", {}, {"tag": 3}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 3, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert result.passed, result.reason
        assert "seq 3" in result.reason, (
            "attribution must bind to the truthy message, not the first False one"
        )

    def test_output_named_like_the_tag_cannot_fake_activation(self):
        """WRONG-IMPL CATCHER: `_signal_value` prefers `outputs` over `io`, so an
        evaluator re-deriving the receipt's value from the merged signal view
        treats a False delivery as activating whenever the acting PLC writes an
        output of the same name. The delivered value must come from the receipt."""
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            (
                "plc_b",
                {"tag": True, "effect": True},
                {},
                _recv(tag=("plc_a", 1, False)),
            ),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed, (
            "a False delivery shadowed by a same-named output must not activate"
        )
        assert "false value" in result.reason

    def test_cause_never_received_on_acting_plc_fails(self):
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": True}, {}, {}),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed
        assert "never received" in result.reason

    def test_receipts_all_false_fails_with_distinct_reason(self):
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 1, False))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed
        assert "false value" in result.reason

    def test_senderless_receipt_is_unattributable(self):
        """Plant-routed and strategy-routed messages carry no sender, so they
        fail by construction rather than by failing to find a matching count."""
        trace = _causal_trace(
            ("plc_a", {}, {}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=(None, 7, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed
        assert "records no sender" in result.reason
        assert "not attributable" in result.reason

    def test_sender_that_recorded_no_such_send_fails(self):
        trace = _causal_trace(
            ("plc_a", {}, {}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 7, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed
        assert "recorded no such send" in result.reason

    def test_colliding_seq_from_another_sender_is_not_attributed(self):
        """WRONG-IMPL CATCHER: counters are per-sender, so seq spaces overlap.
        An evaluator searching all records for a matching count attributes this
        to plc_y, which never sent to plc_b at all."""
        trace = _causal_trace(
            ("plc_y", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=(None, 1, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert not result.passed, (
            "a coincidental seq collision with an unrelated sender must not "
            "manufacture attribution"
        )
        assert "records no sender" in result.reason

    def test_attribution_names_the_recorded_sender_not_a_seq_match(self):
        """Two senders of one key with overlapping seq spaces: attribution must
        follow the sender the receipt names, not whichever record matches first."""
        trace = _causal_trace(
            ("plc_y", {}, {"tag": 1}, {}),
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 1, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert result.passed, result.reason
        assert "sent by 'plc_a'" in result.reason
        assert "plc_y" not in result.reason

    def test_multi_consumer_same_scan_sends_both_attributable(self):
        """One tag with two consumers produces two same-scan sends of the same
        key; `sends` records only the scan's high-water count, so both receipts
        must still resolve to that scan."""
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 2}, {}),
            ("plc_b", {"effect_b": True}, {}, _recv(tag=("plc_a", 1, True))),
            ("plc_c", {"effect_c": True}, {}, _recv(tag=("plc_a", 2, True))),
        )
        for effect in ("effect_b", "effect_c"):
            result = evaluate_assertion(f"CAUSES(tag, {effect})", trace)
            assert result.passed, f"{effect}: {result.reason}"
            assert "sent by 'plc_a'" in result.reason

    def test_observed_gap_ms_is_none(self):
        trace = _causal_trace(
            ("plc_a", {}, {"tag": 1}, {}),
            ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 1, True))),
        )
        result = evaluate_assertion("CAUSES(tag, effect)", trace)
        assert result.passed
        assert result.observed_gap_ms is None

    def test_empty_trace_fails(self):
        result = evaluate_assertion("CAUSES(tag, effect)", TraceLog())
        assert not result.passed
        assert "never became true" in result.reason


class TestBudgetNarrowing:
    """Both grammars make `within:` mandatory, so a parsed assertion always
    carries a budget. These pin the guard that fires if parser and grammar
    ever disagree — it must raise, not silently degrade to a 0ms budget."""

    @pytest.mark.parametrize(
        "form, signals, assertion",
        [
            ("PRECEDES", ("a", "b"), "PRECEDES(a, b, within: 500ms)"),
            ("EVENTUALLY", ("a",), "EVENTUALLY(a, within: 500ms)"),
        ],
    )
    def test_budgetless_parse_raises(self, monkeypatch, form, signals, assertion):
        monkeypatch.setattr(
            verify_assertions,
            "parse_assertion",
            lambda _s: ParsedAssertion(form=form, signals=signals, within_ms=None),
        )
        with pytest.raises(ValueError, match=f"{form} parsed without a budget"):
            evaluate_assertion(assertion, _trace({"a": True, "b": True}))

    @pytest.mark.parametrize(
        "assertion",
        ["PRECEDES(a, b, within: 500ms)", "EVENTUALLY(a, within: 500ms)"],
    )
    def test_parser_always_supplies_a_budget(self, assertion):
        parsed = parse_assertion(assertion)
        assert parsed is not None
        assert parsed.within_ms is not None

    def test_causes_routes_before_the_guard(self):
        """CAUSES carries no budget by design, so the guard must not fire for it
        — it is checked only for the budgeted forms."""
        result = evaluate_assertion(
            "CAUSES(tag, effect)",
            _causal_trace(
                ("plc_a", {}, {"tag": 1}, {}),
                ("plc_b", {"effect": True}, {}, _recv(tag=("plc_a", 1, True))),
            ),
        )
        assert result.passed, result.reason
