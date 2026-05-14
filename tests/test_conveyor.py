from __future__ import annotations
import asyncio
from pathlib import Path

import pytest

from relay.runtime.harness import simulate
from relay.spec.schema import load_spec
from relay.verify.assertions import evaluate_assertion


SCAN_PERIOD_MS = 10.0
MAX_SCANS = 100

_PLC_A_ST = """
IF sensor_a_exit AND NOT handoff_signaled THEN
handoff_signaled := TRUE;
_send_plc_b_handoff_signal := TRUE;
END_IF;
"""

_PLC_B_ST = """
IF handoff_signal AND NOT belt_b_enable THEN
belt_b_enable := TRUE;
END_IF;
"""

_PLC_A_DEAD_ST = """
handoff_signaled := FALSE;
"""


def _spec_path() -> Path:
    return Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"


def _load_spec_and_blocks(plc_a_source: str = _PLC_A_ST, plc_b_source: str = _PLC_B_ST):
    spec = load_spec(_spec_path())
    blocks = {"plc_a": plc_a_source, "plc_b": plc_b_source}
    return spec, blocks


def _run(blocks, max_scans: int = MAX_SCANS):
    spec = load_spec(_spec_path())
    return asyncio.run(simulate(spec, blocks, max_scans=max_scans, scan_period_ms=SCAN_PERIOD_MS))


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
        spec, blocks = _load_spec_and_blocks(plc_a_source=_PLC_A_DEAD_ST)
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


class TestSpecLoading:
    def test_spec_loads_from_yaml(self):
        spec = load_spec(_spec_path())
        assert spec.system_name == "conveyor_handoff"
        assert {p["id"] for p in spec.plcs} == {"plc_a", "plc_b"}
        assert "EVENTUALLY(part_at_b, within: 500ms)" in spec.assertions
        assert "PRECEDES(handoff_signal, belt_b_enable)" in spec.assertions
        assert spec.comm_strategy == "tag"
        assert spec.plant_type == "conveyor"
