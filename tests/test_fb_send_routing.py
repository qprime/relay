from __future__ import annotations

import pytest

from relay.clock import SimClock
from relay.io_image import IOImage
from relay.runtime.comm import CommBuffer
from relay.runtime.fb import FunctionBlock


def _scan(fb: FunctionBlock) -> tuple[IOImage, list]:
    return fb.scan(IOImage.empty(), CommBuffer.empty(), SimClock.zero(), 10.0)


class TestSendRouting:
    def test_send_assignment_routes_to_outgoing(self):
        fb = FunctionBlock(
            source="_send_plc_b_handoff_signal := TRUE;",
            plc_ids=("plc_a", "plc_b"),
        )
        outputs, outgoing = _scan(fb)
        assert outgoing == [("plc_b", "handoff_signal", True)]
        assert "_send_plc_b_handoff_signal" not in outputs.values

    def test_non_send_assignment_stays_as_output(self):
        fb = FunctionBlock(
            source="belt_a_running := TRUE;",
            plc_ids=("plc_a", "plc_b"),
        )
        outputs, outgoing = _scan(fb)
        assert outgoing == []
        assert outputs.get("belt_a_running") is True

    def test_send_to_unknown_plc_raises(self):
        fb = FunctionBlock(
            source="_send_plc_z_signal := TRUE;",
            plc_ids=("plc_a", "plc_b"),
        )
        with pytest.raises(ValueError, match="_send_"):
            _scan(fb)

    def test_send_with_no_plc_ids_raises(self):
        fb = FunctionBlock(source="_send_plc_b_x := TRUE;")
        with pytest.raises(ValueError, match="none registered"):
            _scan(fb)

    def test_longest_plc_id_prefix_wins(self):
        fb = FunctionBlock(
            source="_send_plc_a_b_signal := TRUE;",
            plc_ids=("plc_a", "plc_a_b"),
        )
        _, outgoing = _scan(fb)
        assert outgoing == [("plc_a_b", "signal", True)]

    def test_send_with_empty_key_raises(self):
        fb = FunctionBlock(
            source="_send_plc_b_ := TRUE;",
            plc_ids=("plc_a", "plc_b"),
        )
        with pytest.raises(ValueError, match="_send_"):
            _scan(fb)


class TestScratchSuppression:
    def test_scratch_assignment_never_reaches_output_image(self):
        fb = FunctionBlock(
            source="_scratch_prev_h := TRUE;\nbelt_b_enable := TRUE;",
            plc_ids=("plc_a", "plc_b"),
        )
        outputs, outgoing = _scan(fb)
        assert outgoing == []
        assert dict(outputs.values) == {"belt_b_enable": True}

    def test_scratch_state_still_carries_across_scans(self):
        fb = FunctionBlock(
            source=(
                "_scratch_edge_h := source AND NOT _scratch_prev_h;\n"
                "_scratch_prev_h := source;\n"
                "out := _scratch_edge_h;"
            ),
            plc_ids=("plc_a",),
        )
        io = IOImage.empty().with_value("source", True)
        first, _ = fb.scan(io, CommBuffer.empty(), SimClock.zero(), 10.0)
        second, _ = fb.scan(io, CommBuffer.empty(), SimClock.zero(), 10.0)
        assert first.get("out") is True
        assert second.get("out") is False
        assert "_scratch_prev_h" not in first.values
        assert "_scratch_prev_h" not in second.values

    def test_scratch_names_never_reach_trace(self):
        import asyncio

        import relay.plant  # noqa: F401
        from pathlib import Path
        from relay.runtime.harness import simulate
        from relay.spec.schema import load_spec

        spec_path = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"
        spec = load_spec(spec_path)
        blocks = {
            "plc_a": (
                "_scratch_edge_h := sensor_a_exit AND NOT _scratch_prev_h;\n"
                "_scratch_prev_h := sensor_a_exit;\n"
                "IF _scratch_edge_h THEN\n"
                "_scratch_latched_h := TRUE;\n"
                "END_IF;\n"
                "_send_plc_b_handoff_signal := _scratch_latched_h;"
            ),
            "plc_b": "belt_b_enable := handoff_signal;",
        }
        trace = asyncio.run(simulate(spec, blocks))
        for record in trace.records:
            assert not any(k.startswith("_scratch_") for k in record.outputs.values)
            assert not any(k.startswith("_scratch_") for k in record.io.values)
