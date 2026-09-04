from fractions import Fraction

import pytest

from relay.runtime.can import CanScheduler, encode_can_frame


@pytest.mark.parametrize(
    ("can_id", "value", "crc", "stuffed", "bits"),
    [
        (0x000, False, 17446, 4, 59),
        (0x000, True, 447, 6, 61),
        (0x080, False, 17304, 3, 58),
        (0x080, True, 1537, 4, 59),
        (0x300, False, 21922, 3, 58),
        (0x300, True, 4155, 4, 59),
        (0x7FF, False, 31360, 5, 60),
        (0x7FF, True, 16153, 5, 60),
    ],
)
def test_codec_vectors(can_id, value, crc, stuffed, bits):
    encoded = encode_can_frame(can_id, value)
    assert (encoded.crc, encoded.stuffed_bits, encoded.bit_count) == (crc, stuffed, bits)


def test_simultaneous_frames_arbitrate_by_lowest_id():
    scheduler = CanScheduler(10_000)
    scheduler.enqueue("high", 0x300, True, "a", 1, 0)
    scheduler.enqueue("low", 0x080, True, "b", 1, 0)
    completed = scheduler.settle(20)
    assert [item.frame.signal for item in completed] == ["low", "high"]
    assert completed[1].arbitration_start_ms == completed[0].completion_ms


def test_active_frame_cannot_be_preempted_and_idle_bus_starts_at_ready_time():
    scheduler = CanScheduler(1_000)
    first = scheduler.enqueue("active", 0x300, False, "a", 1, 5)
    scheduler.settle(5)
    scheduler.enqueue("later_priority", 0x000, False, "b", 1, 6)
    completed = scheduler.settle(200)
    assert completed[0].frame == first
    assert completed[0].arbitration_start_ms == Fraction(5)
    assert completed[1].arbitration_start_ms == completed[0].completion_ms


def test_multiple_rounds_and_repeated_values_keep_sequence():
    scheduler = CanScheduler(10_000)
    scheduler.enqueue("x", 1, True, "a", 1, 0)
    scheduler.enqueue("x", 1, True, "a", 2, 10)
    assert [c.frame.seq for c in scheduler.settle(20)] == [1, 2]
