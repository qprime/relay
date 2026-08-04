from __future__ import annotations

from relay.clock import SimClock
from relay.io_image import IOImage
from relay.strategies.assertions import parse_assertion
from relay.trace import ScanRecord, TraceLog
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
