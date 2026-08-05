from __future__ import annotations
import asyncio
import warnings
from pathlib import Path

import pytest

from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import load_spec
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

    def test_conveyor_precedes_observed_gap_is_zero(self):
        spec, blocks = _load_spec_and_blocks()
        trace = asyncio.run(simulate(spec, blocks))
        result = evaluate_assertion(
            "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)", trace
        )
        assert result.observed_gap_ms == 0.0, result.reason

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
