from __future__ import annotations

import asyncio

from relay.runtime.comm import CommBus


def _drain(bus: CommBus, plc_id: str, at_ms: float):
    return asyncio.run(bus.drain(plc_id, at_ms))


def _send(bus: CommBus, **kwargs):
    asyncio.run(bus.send(**kwargs))


class TestDeliveryEligibility:
    """A message becomes visible at the consumer's first scan top whose
    SimClock time is strictly later than the sending scan's."""

    def _bus(self) -> CommBus:
        bus = CommBus()
        bus.register("plc_b")
        return bus

    def test_same_scan_time_is_not_yet_deliverable(self):
        bus = self._bus()
        _send(
            bus,
            to_plc="plc_b",
            key="tag",
            value=True,
            sender="plc_a",
            seq=1,
            sent_elapsed_ms=100.0,
        )
        assert dict(_drain(bus, "plc_b", 100.0).pending) == {}

    def test_next_scan_delivers(self):
        bus = self._bus()
        _send(
            bus,
            to_plc="plc_b",
            key="tag",
            value=True,
            sender="plc_a",
            seq=1,
            sent_elapsed_ms=100.0,
        )
        assert dict(_drain(bus, "plc_b", 100.0).pending) == {}
        assert dict(_drain(bus, "plc_b", 110.0).pending) == {"tag": True}

    def test_deferred_message_survives_the_ineligible_drain(self):
        """An ineligible entry must be requeued, not dropped — the consumer
        drains every scan, and a discarded message would be lost outright."""
        bus = self._bus()
        _send(
            bus,
            to_plc="plc_b",
            key="tag",
            value=True,
            sender="plc_a",
            seq=1,
            sent_elapsed_ms=100.0,
        )
        for _ in range(3):
            assert dict(_drain(bus, "plc_b", 100.0).pending) == {}
        assert dict(_drain(bus, "plc_b", 110.0).pending) == {"tag": True}

    def test_unstamped_message_is_always_eligible(self):
        """Plant and harness routes carry no stamp. A sensor wired to the
        input terminals is sampled at scan top, not delivered over a network."""
        bus = self._bus()
        _send(bus, to_plc="plc_b", key="sensor", value=True, sender=None, seq=1)
        assert dict(_drain(bus, "plc_b", 0.0).pending) == {"sensor": True}

    def test_per_sender_fifo_is_preserved_across_a_deferral(self):
        bus = self._bus()
        for seq, sent in ((1, 90.0), (2, 100.0), (3, 110.0)):
            _send(
                bus,
                to_plc="plc_b",
                key="tag",
                value=seq,
                sender="plc_a",
                seq=seq,
                sent_elapsed_ms=sent,
            )
        first = _drain(bus, "plc_b", 100.0)
        assert first.receipts["tag"].seq == 1
        second = _drain(bus, "plc_b", 110.0)
        assert second.receipts["tag"].seq == 2
        third = _drain(bus, "plc_b", 120.0)
        assert third.receipts["tag"].seq == 3

    def test_a_slower_consumer_collapses_a_burst_to_last_wins(self):
        """Delivery is paced by the consumer's sampling. Several sends inside
        one consumer period land in one drain, and the buffer folds them
        last-wins, exactly as a real input image would."""
        bus = self._bus()
        for seq, sent in ((1, 10.0), (2, 20.0), (3, 30.0)):
            _send(
                bus,
                to_plc="plc_b",
                key="tag",
                value=seq,
                sender="plc_a",
                seq=seq,
                sent_elapsed_ms=sent,
            )
        buf = _drain(bus, "plc_b", 40.0)
        assert dict(buf.pending) == {"tag": 3}
        assert buf.receipts["tag"].seq == 3
