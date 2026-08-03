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
        result = evaluate_assertion("PRECEDES(handoff_signal, belt_b_enable)", trace)
        assert result.passed, result.reason

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
        assert "PRECEDES(handoff_signal, belt_b_enable)" in spec.assertions
        assert spec.comm_strategy == "tag"
        assert spec.plant_type == "conveyor"
