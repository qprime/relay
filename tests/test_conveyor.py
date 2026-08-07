from __future__ import annotations
import asyncio
import warnings
from pathlib import Path

import pytest

from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import TaskSpec, load_spec
from relay.verify.assertions import evaluate_assertion


def _spec_path() -> Path:
    return Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"


def _load_spec_and_blocks(*, silence_plc_a: bool = False):
    spec = load_spec(_spec_path())
    blocks = compile_st_blocks(spec)
    if silence_plc_a:
        blocks["plc_a"] = ""
    return spec, blocks


class TestConveyorHandoff:
    def test_part_arrives_at_b_within_timeout(self):
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion("EVENTUALLY(part_at_b, within: 500ms)", trace)
        assert result.passed, result.reason

    def test_handoff_signal_precedes_belt_b_enable(self):
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion(
            "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)", trace
        )
        assert result.passed, result.reason

    def test_conveyor_precedes_gap_is_one_consumer_scan(self):
        """The bus charges one consumer scan period for delivery (#16), so the
        handoff costs exactly the 10ms scan period rather than nothing."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion(
            "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)", trace
        )
        assert result.observed_gap_ms == 10.0, result.reason

    def test_part_never_arrives_when_sensor_a_never_triggers(self):
        spec, blocks = _load_spec_and_blocks(silence_plc_a=True)
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion("EVENTUALLY(part_at_b, within: 500ms)", trace)
        assert not result.passed, "expected failure when plc_a never signals handoff"

    def test_scan_loop_io_image_immutable_during_execution(self):
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks, max_scans=5))
        for record in trace.records:
            with pytest.raises(TypeError):
                record.io.values["hacked"] = True  # type: ignore[index]
            with pytest.raises(TypeError):
                record.outputs.values["hacked"] = True  # type: ignore[index]

    def test_clock_is_external(self):
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks, max_scans=5))
        plc_a_ticks = [r.clock.tick for r in trace.for_plc("plc_a")]
        assert plc_a_ticks == list(range(5)), f"sim ticks not externally driven: {plc_a_ticks}"


class TestCausesConveyor:
    def test_conveyor_causes_passes(self):
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion("CAUSES(handoff_signal, belt_b_enable)", trace)
        assert result.passed, result.reason
        assert "sent by 'plc_a'" in result.reason

    def test_causes_attributes_to_the_truthy_handoff_not_the_first(self):
        """plc_a sends handoff_signal every scan, False until the part arrives.
        Attribution must land on the activating message, not scan 0's False one."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        first_send = next(
            r for r in trace.for_plc("plc_a") if "handoff_signal" in r.sends
        )
        assert first_send.clock.tick == 0, "expected a send on the very first scan"
        assert not first_send.io.get("handoff_signal"), "scan 0 send should be False"

        acting = next(
            r for r in trace.for_plc("plc_b") if r.outputs.get("belt_b_enable")
        )
        receipt = acting.recvs["handoff_signal"]
        result = evaluate_assertion("CAUSES(handoff_signal, belt_b_enable)", trace)
        assert result.passed, result.reason
        assert f"seq {receipt.seq}" in result.reason
        assert receipt.seq > 1, (
            "attribution bound to the first send; the activating message is later"
        )
        assert receipt.sender == "plc_a"
        assert receipt.value is True

    def test_silenced_producer_fails_causes(self):
        spec, blocks = _load_spec_and_blocks(silence_plc_a=True)
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion("CAUSES(handoff_signal, belt_b_enable)", trace)
        assert not result.passed

    def test_plant_routed_receipts_carry_no_sender_in_a_live_trace(self):
        """The senderless path is exercised end-to-end by crafted traces; here
        the point is that a live plant route really does record sender None,
        so nothing downstream can attribute one to a PLC."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        plant_routed = [
            (r.plc_id, key, receipt)
            for r in trace.records
            for key, receipt in r.recvs.items()
            if key in ("part_at_b", "sensor_a_exit")
        ]
        assert plant_routed, "expected plant-routed receipts in the conveyor trace"
        assert all(receipt.sender is None for _, _, receipt in plant_routed)

        tag_routed = [
            receipt
            for r in trace.records
            for key, receipt in r.recvs.items()
            if key == "handoff_signal"
        ]
        assert tag_routed and all(r.sender == "plc_a" for r in tag_routed), (
            "tag-routed receipts must name their producer"
        )

    def test_causes_verdict_survives_jsonl_round_trip(self):
        import io as io_module

        from relay.trace_io import dump_jsonl, load_jsonl

        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        stream = io_module.StringIO()
        dump_jsonl(trace, stream)
        restored = load_jsonl(io_module.StringIO(stream.getvalue()))
        before = evaluate_assertion("CAUSES(handoff_signal, belt_b_enable)", trace)
        after = evaluate_assertion("CAUSES(handoff_signal, belt_b_enable)", restored)
        assert (before.passed, before.reason) == (after.passed, after.reason)


class TestCausesDeterminism:
    def test_two_runs_produce_identical_sends_and_recvs(self):
        spec, blocks = _load_spec_and_blocks()
        runs = [asyncio.run(simulate(spec, blocks)) for _ in range(2)]
        counters = [
            [
                (r.plc_id, r.clock.tick, dict(r.sends), dict(r.recvs))
                for r in trace.records
            ]
            for trace in runs
        ]
        assert counters[0] == counters[1]
        assert any(sends for _, _, sends, _ in counters[0]), "no counters were recorded"


class TestSubScanTimingWarnings:
    def _spec_with(self, **when_or_emit):
        spec = load_spec(_spec_path())
        trigger = spec.raw["Behavior"]["plc_a"]["triggers"][0]
        for path, value in when_or_emit.items():
            section, field = path.split(".")
            trigger.setdefault(section, {})[field] = value
        return spec

    def _warnings_from(self, spec, scan_period_ms):
        blocks = compile_st_blocks(spec)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            asyncio.run(simulate(spec, blocks, max_scans=3, scan_period_ms=scan_period_ms))
        return [str(w.message) for w in caught]

    def test_no_warning_when_debounce_exceeds_scan_period(self):
        spec = self._spec_with(**{"when.debounce_ms": 20})
        assert self._warnings_from(spec, 10.0) == []

    def test_warns_when_debounce_shorter_than_actual_scan_period(self):
        spec = self._spec_with(**{"when.debounce_ms": 20})
        messages = self._warnings_from(spec, 50.0)
        assert any("debounce_ms is 20ms" in m and "no-op" in m for m in messages)

    def test_warns_when_pulse_duration_shorter_than_actual_scan_period(self):
        spec = self._spec_with(**{"emit.mode": "pulse", "emit.duration_ms": 20})
        messages = self._warnings_from(spec, 50.0)
        assert any("duration_ms is 20ms" in m for m in messages)

    def test_no_warning_for_zero_debounce(self):
        spec = self._spec_with(**{"when.debounce_ms": 0})
        assert self._warnings_from(spec, 50.0) == []


class TestSpecLoading:
    def test_spec_loads_from_yaml(self):
        spec = load_spec(_spec_path())
        assert spec.system_name == "conveyor_handoff"
        assert {p["id"] for p in spec.plcs} == {"plc_a", "plc_b"}
        assert "EVENTUALLY(part_at_b, within: 500ms)" in spec.assertions
        assert "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)" in spec.assertions
        assert "CAUSES(handoff_signal, belt_b_enable)" in spec.assertions
        assert spec.comm_strategy == "tag"
        assert spec.plant_type == "conveyor"


class TestCausesSpecValidation:
    def _spec_text_with(self, assertion: str, tmp_path: Path) -> Path:
        import yaml

        raw = yaml.safe_load(_spec_path().read_text())
        raw["Assertions"] = [assertion]
        out = tmp_path / "spec.yaml"
        out.write_text(yaml.safe_dump(raw))
        return out

    def test_causes_on_undeclared_tag_rejected_at_load(self, tmp_path):
        """part_at_b is plant-routed, not a tag, so it carries no attributable
        sender — the failure belongs at load, not at verification time."""
        path = self._spec_text_with("CAUSES(part_at_b, belt_b_enable)", tmp_path)
        with pytest.raises(ValueError) as exc:
            load_spec(path)
        assert "part_at_b" in str(exc.value)
        assert "Comm.tags" in str(exc.value)

    def test_causes_self_reference_rejected_at_load(self, tmp_path):
        path = self._spec_text_with(
            "CAUSES(handoff_signal, handoff_signal)", tmp_path
        )
        with pytest.raises(ValueError) as exc:
            load_spec(path)
        assert "cannot cause itself" in str(exc.value)

    def test_declared_tag_cause_loads(self, tmp_path):
        path = self._spec_text_with("CAUSES(handoff_signal, belt_b_enable)", tmp_path)
        assert load_spec(path).assertions == ["CAUSES(handoff_signal, belt_b_enable)"]


class TestCommTagLatencyIsMeasurable:
    """#21: a comm tag's send-side emission must be visible to the verifier.

    Before this, a tag resolved only through the consumer's I/O image — in the
    scan where it was promoted and acted on — so PRECEDES read both endpoints
    off one ScanRecord and compared a clock with itself. These tests run a real
    two-PLC spec end to end; the pre-existing PRECEDES semantics tests build
    synthetic traces that never touch a comm tag, so they pass either way.
    """

    def _consumer_debounced(self, tmp_path: Path, debounce_ms: int):
        import yaml

        raw = yaml.safe_load(
            (Path(__file__).parent.parent / "specs" / "conveyor_pulse_release.yaml")
            .read_text()
        )
        raw["System"]["name"] = f"consumer_debounced_{debounce_ms}"
        raw["Behavior"]["plc_a"]["triggers"][0]["when"].pop("debounce_ms", None)
        raw["Behavior"]["plc_b"]["triggers"][0]["when"]["debounce_ms"] = debounce_ms
        out = tmp_path / "spec.yaml"
        out.write_text(yaml.safe_dump(raw))
        return load_spec(out)

    def test_known_nonzero_gap_is_reported_exactly(self, tmp_path):
        """The consumer cannot act in the producer's send scan, so a real gap
        exists and is derivable: plc_a first sends truthy at 100.0ms, delivery
        costs one 10ms consumer scan, plc_b's 30ms debounce spans two more
        scans, and the pulse asserts at 130.0ms."""
        spec = self._consumer_debounced(tmp_path, 30)
        trace = asyncio.run(simulate(spec, compile_st_blocks(spec)))
        result = evaluate_assertion(
            "PRECEDES(release_request, belt_b_enable, within: 500ms)", trace
        )
        assert result.passed, result.reason
        assert result.observed_gap_ms == 30.0, result.reason

    def test_gap_is_anchored_to_the_first_truthy_send(self, tmp_path):
        """The failure a passing-looking implementation ships. plc_a sends
        every scan from tick 0 carrying False; anchoring to the first send of
        any value would report 130.0ms and measure time since a message that
        said nothing happened."""
        spec = self._consumer_debounced(tmp_path, 30)
        trace = asyncio.run(simulate(spec, compile_st_blocks(spec)))
        sends = [
            r for r in trace.for_plc("plc_a") if "release_request" in r.sends
        ]
        assert not sends[0].sends["release_request"].value, (
            "fixture must include false sends before the real event"
        )
        result = evaluate_assertion(
            "PRECEDES(release_request, belt_b_enable, within: 500ms)", trace
        )
        assert result.observed_gap_ms != 130.0, (
            "gap anchored to the producer's first False send"
        )
        assert result.observed_gap_ms == 30.0

    def test_conveyor_handoff_gap_is_the_charged_delivery_latency(self):
        """The endpoint is plc_a's first truthy send, not plc_b's delivery, so
        the gap now measures what the bus charges: one consumer scan period.
        Before #16 this read 0.0 because the bus charged nothing."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion(
            "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)", trace
        )
        assert result.observed_gap_ms == 10.0
        first_truthy = next(
            r for r in trace.for_plc("plc_a")
            if "handoff_signal" in r.sends and r.sends["handoff_signal"].value
        )
        assert first_truthy.clock.elapsed_ms == 100.0
        assert "at 100.0ms precedes" in result.reason

    def test_tag_endpoint_reads_the_producer_not_the_consumer(self):
        """A tag never enters the producer's output image and is delivered into
        the consumer's, so the merged signal view resolves it on the consumer.
        The endpoint must come from the producer's sends instead."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        assert all(
            "handoff_signal" not in r.outputs.values for r in trace.for_plc("plc_a")
        )
        assert any(
            "handoff_signal" in r.sends for r in trace.for_plc("plc_a")
        )
        assert all("handoff_signal" not in r.sends for r in trace.for_plc("plc_b"))

    def test_producer_side_pair_no_longer_resolves_cross_plc(self):
        """Both names must resolve on plc_a. Before this, sensor_a_exit read
        from plc_a's image and handoff_signal from plc_b's, so the assertion
        read as a producer-side latency claim and silently measured a
        cross-PLC delivery instead."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion(
            "PRECEDES(sensor_a_exit, handoff_signal, within: 500ms)", trace
        )
        arrival = next(
            r for r in trace.for_plc("plc_a") if r.io.get("sensor_a_exit")
        )
        emission = next(
            r for r in trace.for_plc("plc_a")
            if "handoff_signal" in r.sends and r.sends["handoff_signal"].value
        )
        assert arrival.clock.elapsed_ms == emission.clock.elapsed_ms
        assert result.observed_gap_ms == 0.0

    def test_multi_consumer_send_stores_high_water_count_and_last_value(
        self, tmp_path
    ):
        """A tag with two consumers emits two messages of one key per scan, and
        `sends` holds one entry: the scan's high-water count and the last value
        written. Pinned rather than inherited from CAUSES's comment, since the
        endpoint rule now reads `value` from here."""
        import yaml

        raw = yaml.safe_load(_spec_path().read_text())
        raw["System"]["name"] = "multi_consumer_probe"
        raw["System"]["plcs"].append({"id": "plc_c", "role": "downstream"})
        raw["Comm"]["tags"][0]["consumed_by"] = ["plc_b", "plc_c"]
        raw["Behavior"]["plc_c"] = {
            "triggers": [
                {
                    "id": "latch_on_handoff",
                    "when": {"signal": "handoff_signal", "edge": "rising"},
                    "emit": {"output": "c_belt_enable", "mode": "latched"},
                }
            ]
        }
        out = tmp_path / "spec.yaml"
        out.write_text(yaml.safe_dump(raw))
        spec = load_spec(out)
        trace = asyncio.run(simulate(spec, compile_st_blocks(spec), max_scans=20))

        counts = [
            r.sends["handoff_signal"].count
            for r in trace.for_plc("plc_a")
            if "handoff_signal" in r.sends
        ]
        assert counts[:2] == [2, 4], "two consumers means two sends per scan"

        result = evaluate_assertion(
            "PRECEDES(handoff_signal, c_belt_enable, within: 500ms)", trace
        )
        assert result.passed, result.reason
        assert result.observed_gap_ms == 10.0

    def test_eventually_resolves_a_tag_the_same_way_precedes_does(self):
        """Both forms must read a tag on the same side. Leaving EVENTUALLY on
        the merged signal view would make one name mean the producer's
        emission in one form and the consumer's delivery in another — the
        divergence becomes observable once the bus charges delivery latency."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        eventually = evaluate_assertion(
            "EVENTUALLY(handoff_signal, within: 500ms)", trace
        )
        precedes = evaluate_assertion(
            "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)", trace
        )
        assert eventually.passed and precedes.passed
        emission = next(
            r for r in trace.for_plc("plc_a")
            if "handoff_signal" in r.sends and r.sends["handoff_signal"].value
        )
        assert f"true at {emission.clock.elapsed_ms:.1f}ms" in eventually.reason
        assert f"at {emission.clock.elapsed_ms:.1f}ms precedes" in precedes.reason


class TestBusChargesDeliveryLatency:
    """#16 item 1: a message sent during a scan must not be readable by a
    consumer scan at the same SimClock time.

    Before this, the live delivery path was the PLC's own in-scan `bus.send`,
    and coroutines ran in `System.plcs` declaration order with nothing in the
    scan path suspending. A producer declared before its consumer completed
    its whole scan, send included, before the consumer's drain ran — so
    latency was zero along declaration order and one scan against it.
    """

    def _receipt_lag_ticks(self, spec):
        trace = asyncio.run(simulate(spec, compile_st_blocks(spec)))
        sends = {
            r.sends["handoff_signal"].count: r.clock.tick
            for r in trace.for_plc("plc_a")
            if "handoff_signal" in r.sends
        }
        lags = [
            r.clock.tick - sends[r.recvs["handoff_signal"].seq]
            for r in trace.for_plc("plc_b")
            if "handoff_signal" in r.recvs
            and r.recvs["handoff_signal"].seq in sends
        ]
        assert lags, "no receipt was matched to a send"
        return set(lags)

    def test_receipt_never_lands_in_the_sending_tick(self):
        spec, _ = _load_spec_and_blocks()
        assert self._receipt_lag_ticks(spec) == {1}

    def test_latency_is_identical_in_both_declaration_orders(self):
        """The test that kills the ordering dependence. Reversing System.plcs
        must not change observable timing; before the fix it flipped delivery
        between zero and one scan."""
        import yaml

        forward = load_spec(_spec_path())
        raw = yaml.safe_load(_spec_path().read_text())
        raw["System"]["plcs"] = list(reversed(raw["System"]["plcs"]))
        reversed_spec = TaskSpec(raw=raw)

        assert self._receipt_lag_ticks(forward) == {1}
        assert self._receipt_lag_ticks(reversed_spec) == {1}

    def test_gap_is_identical_in_both_declaration_orders(self):
        import yaml

        raw = yaml.safe_load(_spec_path().read_text())
        raw["System"]["plcs"] = list(reversed(raw["System"]["plcs"]))
        assertion = "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)"

        forward_spec, forward_blocks = _load_spec_and_blocks()
        forward = evaluate_assertion(
            assertion, asyncio.run(simulate(forward_spec, forward_blocks))
        )
        rev_spec = TaskSpec(raw=raw)
        rev = evaluate_assertion(
            assertion,
            asyncio.run(simulate(rev_spec, compile_st_blocks(rev_spec))),
        )
        assert forward.observed_gap_ms == 10.0
        assert rev.observed_gap_ms == forward.observed_gap_ms

    def test_plant_routes_are_exempt_from_the_charge(self):
        """A plant route models a sensor wired to the input terminals and
        sampled at scan top, not a network. Plant sends carry no stamp and
        deliver at the consumer's next drain, as before."""
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        first_sensor = next(
            r for r in trace.for_plc("plc_a") if r.io.get("sensor_a_exit")
        )
        assert first_sensor.clock.tick == 10, (
            "a charged plant route would push the sensor a scan later"
        )
