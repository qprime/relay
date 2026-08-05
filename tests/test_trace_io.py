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
from relay.trace import Receipt, ScanRecord, TraceLog
from relay.trace_io import dump_jsonl, load_jsonl, record_from_dict, record_to_dict
from relay.verify.assertions import evaluate_all


_SPEC_PATH = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"
_GOLDEN_TRACE = Path(__file__).parent / "golden" / "conveyor_trace.jsonl"

_MINIMAL_RECORD = {
    "plc_id": "plc_a",
    "tick": 0,
    "elapsed_ms": 0.0,
    "io_snapshot": {},
    "outputs": {},
    "sends": {},
    "recvs": {},
}


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
        a
        for a in spec.assertions
        if (parsed := parse_assertion(a)) is not None and parsed.form == "PRECEDES"
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

    def test_each_line_has_seven_required_keys(self):
        _, trace = _conveyor_spec_and_trace()
        for line in _dump_to_text(trace).splitlines():
            assert set(json.loads(line)) == {
                "plc_id",
                "tick",
                "elapsed_ms",
                "io_snapshot",
                "outputs",
                "sends",
                "recvs",
            }

    def test_sends_and_recvs_round_trip(self):
        _, trace = _conveyor_spec_and_trace()
        restored = _round_trip(trace)
        for original, loaded in zip(trace.records, restored.records):
            assert dict(loaded.sends) == dict(original.sends)
            assert dict(loaded.recvs) == dict(original.recvs)
            for seq in loaded.sends.values():
                assert isinstance(seq, int) and not isinstance(seq, bool)
            for receipt in loaded.recvs.values():
                assert isinstance(receipt.seq, int)
                assert receipt.sender is None or isinstance(receipt.sender, str)
        assert any(r.sends for r in restored.records), "no send was recorded to compare"
        assert any(r.recvs for r in restored.records), "no receipt was recorded to compare"

    def test_receipt_carries_sender_and_delivered_value(self):
        """Attribution needs identity on the receipt, not just a counter: the
        merged signal view cannot say who sent it or what it delivered."""
        _, trace = _conveyor_spec_and_trace()
        acting = next(
            r for r in _round_trip(trace).for_plc("plc_b")
            if r.outputs.get("belt_b_enable")
        )
        receipt = acting.recvs["handoff_signal"]
        assert receipt.sender == "plc_a"
        assert receipt.value is True
        assert receipt.seq > 1

    def test_plant_routed_receipt_records_no_sender(self):
        _, trace = _conveyor_spec_and_trace()
        received = [
            r.recvs["part_at_b"]
            for r in _round_trip(trace).for_plc("plc_b")
            if "part_at_b" in r.recvs
        ]
        assert received, "expected plant-routed part_at_b receipts"
        assert all(r.sender is None for r in received), (
            "plant-routed messages have no PLC sender and must serialize as null"
        )

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

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
    )
    def test_non_finite_float_raises_at_dump(self, value):
        trace = TraceLog([
            ScanRecord(
                plc_id="plc_a",
                clock=SimClock(tick=0, elapsed_ms=0.0),
                io=IOImage(values={"level": value}),
                outputs=IOImage.empty(),
            )
        ])
        with pytest.raises(ValueError) as exc:
            _dump_to_text(trace)
        assert "level" in str(exc.value)

    def test_integral_elapsed_ms_loads_as_float(self):
        record = record_from_dict(
            {
                "plc_id": "plc_a",
                "tick": 10,
                "elapsed_ms": 100,
                "io_snapshot": {},
                "outputs": {},
                "sends": {},
                "recvs": {},
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
                "sends": {},
                "recvs": {},
            }
        )
        assert isinstance(record.clock.tick, int)

    def test_seq_counters_load_as_ints(self):
        record = record_from_dict(
            {
                **_MINIMAL_RECORD,
                "sends": {"handoff_signal": 11},
                "recvs": {"sensor_a_exit": {"sender": None, "seq": 1, "value": True}},
            }
        )
        assert record.sends["handoff_signal"] == 11
        assert isinstance(record.sends["handoff_signal"], int)
        assert isinstance(record.recvs["sensor_a_exit"].seq, int)

    @pytest.mark.parametrize("value", [11.0, 0.9, True, "11", None])
    def test_non_int_send_counter_is_rejected_not_truncated(self, value):
        """int() as a load guard always succeeds, so it converts a corrupt
        count into a plausible one: 0.9 became 0 and True became 1. `sends`
        feeds CAUSES attribution, so a truncated count silently changes which
        sender scan is credited. No conformant writer emits a non-int here —
        json.dumps of an int emits 11, and the host writes std::to_string of
        an int64."""
        with pytest.raises(ValueError) as exc:
            _load_from_text(
                json.dumps({**_MINIMAL_RECORD, "sends": {"handoff_signal": value}})
                + "\n"
            )
        assert "handoff_signal" in str(exc.value)

    @pytest.mark.parametrize("value", [1.0, True, "1", None])
    def test_non_int_receipt_seq_is_rejected_not_truncated(self, value):
        with pytest.raises(ValueError) as exc:
            _load_from_text(
                json.dumps(
                    {
                        **_MINIMAL_RECORD,
                        "recvs": {"tag": {"sender": "plc_a", "seq": value, "value": True}},
                    }
                )
                + "\n"
            )
        assert "tag" in str(exc.value)

    def test_send_counter_round_trips_as_written(self):
        """Dump accepts what load accepts: bool and float pass the value-type
        check for signals but are not counters, so guarding only one side let
        {'go': True} dump as true and reload as 1."""
        record = ScanRecord(
            plc_id="plc_a",
            clock=SimClock(tick=0, elapsed_ms=0.0),
            io=IOImage.empty(),
            outputs=IOImage.empty(),
            sends={"handoff_signal": 11},
        )
        restored = _round_trip(TraceLog([record])).records[0]
        assert restored.sends == {"handoff_signal": 11}

    @pytest.mark.parametrize("value", [True, 0.9])
    def test_non_int_send_counter_rejected_at_dump(self, value):
        trace = TraceLog([
            ScanRecord(
                plc_id="plc_a",
                clock=SimClock(tick=0, elapsed_ms=0.0),
                io=IOImage.empty(),
                outputs=IOImage.empty(),
                sends={"handoff_signal": value},
            )
        ])
        with pytest.raises(TypeError) as exc:
            _dump_to_text(trace)
        assert "handoff_signal" in str(exc.value)

    @pytest.mark.parametrize("missing", ["sender", "seq", "value"])
    def test_receipt_missing_field_raises_naming_key(self, missing):
        full = {"sender": "plc_a", "seq": 1, "value": True}
        with pytest.raises(KeyError) as exc:
            _load_from_text(
                json.dumps(
                    {
                        **_MINIMAL_RECORD,
                        "recvs": {
                            "tag": {k: v for k, v in full.items() if k != missing}
                        },
                    }
                )
                + "\n"
            )
        assert missing in str(exc.value)

    def test_scalar_receipt_is_rejected(self):
        """The counter-only shape carries no sender or delivered value, so a
        trace written to it cannot support attribution — reject, don't coerce."""
        with pytest.raises(ValueError) as exc:
            _load_from_text(
                json.dumps({**_MINIMAL_RECORD, "recvs": {"tag": 3}}) + "\n"
            )
        assert "not an object" in str(exc.value)

    def test_non_string_sender_is_rejected(self):
        with pytest.raises(ValueError) as exc:
            _load_from_text(
                json.dumps(
                    {
                        **_MINIMAL_RECORD,
                        "recvs": {"tag": {"sender": 7, "seq": 1, "value": True}},
                    }
                )
                + "\n"
            )
        assert "sender" in str(exc.value)

    def test_unserializable_delivered_value_raises_at_dump(self):
        trace = TraceLog([
            ScanRecord(
                plc_id="plc_b",
                clock=SimClock(tick=0, elapsed_ms=0.0),
                io=IOImage.empty(),
                outputs=IOImage.empty(),
                recvs={"tag": Receipt(sender="plc_a", seq=1, value="running")},
            )
        ])
        with pytest.raises(TypeError) as exc:
            _dump_to_text(trace)
        assert "tag" in str(exc.value)


class TestTraceIOLoadGuards:
    """The dump-side guard the module already owns, applied to the side that
    actually faces untrusted bytes: the C++ host writes traces the Python
    verifier reads, so load is where a foreign writer's values arrive."""

    @pytest.mark.parametrize("where", ["io_snapshot", "outputs"])
    @pytest.mark.parametrize("value", ["true", None, [1], {"a": 1}])
    def test_disallowed_signal_value_rejected_at_load(self, where, value):
        with pytest.raises(ValueError) as exc:
            _load_from_text(
                json.dumps({**_MINIMAL_RECORD, where: {"part_at_b": value}}) + "\n"
            )
        assert "part_at_b" in str(exc.value)
        assert type(value).__name__ in str(exc.value)

    @pytest.mark.parametrize("where", ["io_snapshot", "outputs"])
    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_signal_value_rejected_at_load(self, where, literal):
        fields = {k: v for k, v in _MINIMAL_RECORD.items() if k != where}
        record = json.dumps(fields)[:-1]
        with pytest.raises(ValueError) as exc:
            _load_from_text(f'{record}, "{where}": {{"level": {literal}}}}}\n')
        assert "level" in str(exc.value)

    @pytest.mark.parametrize("where", ["io_snapshot", "outputs"])
    def test_non_object_signal_map_rejected_at_load(self, where):
        with pytest.raises(ValueError) as exc:
            _load_from_text(json.dumps({**_MINIMAL_RECORD, where: [1, 2]}) + "\n")
        assert where in str(exc.value)

    @pytest.mark.parametrize("value", ["true", None, [1]])
    def test_disallowed_delivered_value_rejected_at_load(self, value):
        with pytest.raises(ValueError) as exc:
            _load_from_text(
                json.dumps(
                    {
                        **_MINIMAL_RECORD,
                        "recvs": {"tag": {"sender": "plc_a", "seq": 1, "value": value}},
                    }
                )
                + "\n"
            )
        assert "tag" in str(exc.value)

    def test_string_signal_value_does_not_reach_the_verifier(self):
        """A str where the verifier expects bool is truthy for any non-empty
        string, so 'false' would read as a satisfied signal."""
        with pytest.raises(ValueError):
            _load_from_text(
                json.dumps({**_MINIMAL_RECORD, "io_snapshot": {"part_at_b": "false"}})
                + "\n"
            )

    @pytest.mark.parametrize("where", ["sends", "recvs"])
    def test_non_object_counter_map_rejected_with_position(self, where):
        """A bare AttributeError from .items() escapes load_jsonl's
        (KeyError, TypeError, ValueError) handler, losing the line number."""
        with pytest.raises(ValueError) as exc:
            _load_from_text(json.dumps({**_MINIMAL_RECORD, where: [1, 2]}) + "\n")
        assert where in str(exc.value)
        assert "line 1" in str(exc.value)

    @pytest.mark.parametrize("value", [7, None, ["plc_a"]])
    def test_non_string_plc_id_rejected_at_load(self, value):
        """plc_id is the key TraceLog.for_plc matches on and the key CAUSES
        uses to locate the sender's scans, so a non-string yields an empty
        match set and an unattributable-looking verdict."""
        with pytest.raises(ValueError) as exc:
            _load_from_text(json.dumps({**_MINIMAL_RECORD, "plc_id": value}) + "\n")
        assert "plc_id" in str(exc.value)

    def test_non_string_plc_id_rejected_at_dump(self):
        trace = TraceLog([
            ScanRecord(
                plc_id=7,  # pyright: ignore[reportArgumentType]
                clock=SimClock(tick=0, elapsed_ms=0.0),
                io=IOImage.empty(),
                outputs=IOImage.empty(),
            )
        ])
        with pytest.raises(TypeError) as exc:
            _dump_to_text(trace)
        assert "plc_id" in str(exc.value)

    def test_guard_failure_names_the_file_line_number(self):
        good = json.dumps(_MINIMAL_RECORD)
        bad = json.dumps({**_MINIMAL_RECORD, "outputs": {"belt": "on"}})
        with pytest.raises(ValueError) as exc:
            _load_from_text(f"{good}\n{good}\n{bad}\n{good}\n")
        assert "line 3" in str(exc.value)


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
        good = json.dumps(_MINIMAL_RECORD)
        text = f"{good}\n{good}\n{{not json\n{good}\n"
        with pytest.raises(ValueError) as exc:
            _load_from_text(text)
        assert "line 3" in str(exc.value)

    @pytest.mark.parametrize(
        "bad,kind",
        [("[1, 2, 3]", "list"), ("42", "int"), ('"hello"', "str"), ("null", "NoneType")],
    )
    def test_non_object_line_raises_naming_file_line_number(self, bad, kind):
        good = json.dumps(_MINIMAL_RECORD)
        with pytest.raises(ValueError) as exc:
            _load_from_text(f"{good}\n{good}\n{bad}\n{good}\n")
        assert "line 3" in str(exc.value)
        assert f"JSON {kind}, not an object" in str(exc.value), (
            "a non-object line must be diagnosed as such, not as a Python "
            "subscripting error leaking through the generic field handler"
        )

    @pytest.mark.parametrize(
        "field,value",
        [("tick", "abc"), ("elapsed_ms", None), ("tick", None), ("elapsed_ms", "x")],
    )
    def test_unreadable_clock_field_raises_naming_file_line_number(self, field, value):
        good = json.dumps(_MINIMAL_RECORD)
        bad = json.dumps({**_MINIMAL_RECORD, field: value})
        with pytest.raises(ValueError) as exc:
            _load_from_text(f"{good}\n{good}\n{bad}\n{good}\n")
        assert "line 3" in str(exc.value)

    @pytest.mark.parametrize("missing", ["io_snapshot", "outputs", "sends", "recvs"])
    def test_missing_required_key_raises_naming_key(self, missing):
        text = json.dumps({k: v for k, v in _MINIMAL_RECORD.items() if k != missing})
        with pytest.raises(KeyError) as exc:
            _load_from_text(text + "\n")
        assert missing in str(exc.value)
        assert "line 1" in str(exc.value)

    def test_counterless_line_is_rejected_not_tolerated(self):
        """A five-key trace carries no attribution, so CAUSES on it would fail
        with 'never received' — a wrong-reason verdict. Loud KeyError instead."""
        legacy = {
            k: v for k, v in _MINIMAL_RECORD.items() if k not in ("sends", "recvs")
        }
        with pytest.raises(KeyError) as exc:
            _load_from_text(json.dumps(legacy) + "\n")
        assert "sends" in str(exc.value)


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
