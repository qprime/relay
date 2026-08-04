from __future__ import annotations
import asyncio
import io as io_module
import json
from pathlib import Path

import pytest

from relay.clock import SimClock
from relay.generator.st import compile_st_blocks
from relay.io_image import IOImage
from relay.runtime.harness import simulate
from relay.spec.schema import load_spec
from relay.strategies.assertions import parse_assertion
from relay.trace import ScanRecord, TraceLog
from relay.trace_io import dump_jsonl, load_jsonl, record_from_dict, record_to_dict
from relay.verify.assertions import evaluate_all


_SPEC_PATH = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"
_GOLDEN_TRACE = Path(__file__).parent / "golden" / "conveyor_trace.jsonl"


def _conveyor_spec_and_trace():
    spec = load_spec(_SPEC_PATH)
    trace = asyncio.run(simulate(spec, compile_st_blocks(spec)))
    return spec, trace


def _dump_to_text(trace: TraceLog) -> str:
    stream = io_module.StringIO()
    dump_jsonl(trace, stream)
    return stream.getvalue()


def _load_from_text(text: str) -> TraceLog:
    return load_jsonl(io_module.StringIO(text))


def _round_trip(trace: TraceLog) -> TraceLog:
    return _load_from_text(_dump_to_text(trace))


def _precedes_assertion(spec) -> str:
    return next(
        a for a in spec.assertions if parse_assertion(a).form == "PRECEDES"
    )


class TestTraceIORoundTrip:
    def test_conveyor_trace_survives_round_trip(self):
        _, trace = _conveyor_spec_and_trace()
        loaded = _round_trip(trace)
        assert len(loaded.records) == len(trace.records)
        for original, restored in zip(trace.records, loaded.records):
            assert restored.plc_id == original.plc_id
            assert restored.clock.tick == original.clock.tick
            assert restored.clock.elapsed_ms == original.clock.elapsed_ms
            assert dict(restored.io.values) == dict(original.io.values)
            assert dict(restored.outputs.values) == dict(original.outputs.values)

    def test_assertion_results_identical_after_round_trip(self):
        spec, trace = _conveyor_spec_and_trace()
        before = evaluate_all(spec.assertions, trace)
        after = evaluate_all(spec.assertions, _round_trip(trace))
        assert [(r.assertion, r.passed, r.reason, r.observed_gap_ms) for r in before] == [
            (r.assertion, r.passed, r.reason, r.observed_gap_ms) for r in after
        ]

    def test_empty_trace_round_trips(self):
        assert _round_trip(TraceLog()).records == []

    def test_empty_io_image_round_trips(self):
        trace = TraceLog([
            ScanRecord(
                plc_id="plc_a",
                clock=SimClock(tick=0, elapsed_ms=0.0),
                io=IOImage.empty(),
                outputs=IOImage.empty(),
            )
        ])
        restored = _round_trip(trace).records[0]
        assert dict(restored.io.values) == {}
        assert dict(restored.outputs.values) == {}


class TestTraceIOFormat:
    def test_record_dict_round_trips_without_a_stream(self):
        record = ScanRecord(
            plc_id="plc_b",
            clock=SimClock(tick=10, elapsed_ms=100.0),
            io=IOImage(values={"handoff_signal": True, "belt_b_enable": False}),
            outputs=IOImage(values={"belt_b_enable": True}),
        )
        assert record_from_dict(record_to_dict(record)) == record

    def test_line_count_equals_record_count(self):
        _, trace = _conveyor_spec_and_trace()
        lines = _dump_to_text(trace).splitlines()
        assert len(lines) == len(trace.records)

    def test_each_line_has_five_required_keys(self):
        _, trace = _conveyor_spec_and_trace()
        for line in _dump_to_text(trace).splitlines():
            assert set(json.loads(line)) == {
                "plc_id",
                "tick",
                "elapsed_ms",
                "io_snapshot",
                "outputs",
            }

    def test_key_order_does_not_affect_bytes(self):
        def _record(values):
            return ScanRecord(
                plc_id="plc_a",
                clock=SimClock(tick=1, elapsed_ms=10.0),
                io=IOImage(values=values),
                outputs=IOImage.empty(),
            )

        forward = _record({"alpha": True, "beta": False, "gamma": True})
        reverse = _record({"gamma": True, "beta": False, "alpha": True})
        assert _dump_to_text(TraceLog([forward])) == _dump_to_text(TraceLog([reverse]))

    def test_blank_lines_are_skipped_on_load(self):
        _, trace = _conveyor_spec_and_trace()
        text = _dump_to_text(trace)
        padded = "\n" + text.replace("\n", "\n\n") + "\n"
        assert len(_load_from_text(padded).records) == len(trace.records)


class TestTraceIOTypes:
    def _round_trip_value(self, value):
        trace = TraceLog([
            ScanRecord(
                plc_id="plc_a",
                clock=SimClock(tick=0, elapsed_ms=0.0),
                io=IOImage(values={"signal": value}),
                outputs=IOImage.empty(),
            )
        ])
        return _round_trip(trace).records[0].io.get("signal")

    def test_bool_survives_as_bool(self):
        restored = self._round_trip_value(True)
        assert isinstance(restored, bool)
        assert restored is True

    def test_int_survives_as_int(self):
        restored = self._round_trip_value(7)
        assert isinstance(restored, int) and not isinstance(restored, bool)
        assert restored == 7

    def test_float_survives_as_float(self):
        restored = self._round_trip_value(1.5)
        assert isinstance(restored, float)
        assert restored == 1.5

    @pytest.mark.parametrize("value", ["running", None])
    def test_disallowed_value_type_raises_at_dump(self, value):
        trace = TraceLog([
            ScanRecord(
                plc_id="plc_a",
                clock=SimClock(tick=0, elapsed_ms=0.0),
                io=IOImage(values={"mode": value}),
                outputs=IOImage.empty(),
            )
        ])
        with pytest.raises(TypeError) as exc:
            _dump_to_text(trace)
        assert "mode" in str(exc.value)
        assert type(value).__name__ in str(exc.value)

    def test_integral_elapsed_ms_loads_as_float(self):
        record = record_from_dict(
            {
                "plc_id": "plc_a",
                "tick": 10,
                "elapsed_ms": 100,
                "io_snapshot": {},
                "outputs": {},
            }
        )
        assert isinstance(record.clock.elapsed_ms, float)

    def test_tick_loads_as_int(self):
        record = record_from_dict(
            {
                "plc_id": "plc_a",
                "tick": 10.0,
                "elapsed_ms": 100.0,
                "io_snapshot": {},
                "outputs": {},
            }
        )
        assert isinstance(record.clock.tick, int)


class TestGoldenTrace:
    def test_conveyor_trace_matches_golden(self):
        _, trace = _conveyor_spec_and_trace()
        assert _dump_to_text(trace) == _GOLDEN_TRACE.read_text()

    def test_golden_trace_loads_and_verifies(self):
        spec, trace = _conveyor_spec_and_trace()
        with _GOLDEN_TRACE.open() as stream:
            from_disk = load_jsonl(stream)
        assert [
            (r.assertion, r.passed, r.reason, r.observed_gap_ms)
            for r in evaluate_all(spec.assertions, from_disk)
        ] == [
            (r.assertion, r.passed, r.reason, r.observed_gap_ms)
            for r in evaluate_all(spec.assertions, trace)
        ]


class TestTraceIOErrors:
    def test_malformed_line_raises_naming_file_line_number(self):
        good = json.dumps(
            {
                "plc_id": "plc_a",
                "tick": 0,
                "elapsed_ms": 0.0,
                "io_snapshot": {},
                "outputs": {},
            }
        )
        text = f"{good}\n{good}\n{{not json\n{good}\n"
        with pytest.raises(ValueError) as exc:
            _load_from_text(text)
        assert "line 3" in str(exc.value)

    def test_missing_required_key_raises_naming_key(self):
        text = json.dumps(
            {"plc_id": "plc_a", "tick": 0, "elapsed_ms": 0.0, "outputs": {}}
        )
        with pytest.raises(KeyError) as exc:
            _load_from_text(text + "\n")
        assert "io_snapshot" in str(exc.value)


class TestIOSnapshotIsLoadBearing:
    def test_dropping_io_snapshot_breaks_precedes(self):
        spec, trace = _conveyor_spec_and_trace()
        stripped = "\n".join(
            json.dumps({**json.loads(line), "io_snapshot": {}}, sort_keys=True)
            for line in _dump_to_text(trace).splitlines()
        )
        assertion = _precedes_assertion(spec)
        before = evaluate_all([assertion], trace)[0]
        after = evaluate_all([assertion], _load_from_text(stripped))[0]
        assert before.passed
        assert not after.passed, (
            "io_snapshot is load-bearing: handoff_signal is never a plc_b output, "
            "so PRECEDES must fail once the io snapshot is dropped"
        )

    def test_handoff_signal_absent_from_plc_b_outputs(self):
        _, trace = _conveyor_spec_and_trace()
        assert all(
            "handoff_signal" not in r.outputs.values for r in trace.for_plc("plc_b")
        )

    def test_handoff_signal_present_in_plc_b_io(self):
        _, trace = _conveyor_spec_and_trace()
        assert any(r.io.get("handoff_signal") for r in trace.for_plc("plc_b"))
