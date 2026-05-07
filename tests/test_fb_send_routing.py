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
