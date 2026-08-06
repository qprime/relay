from __future__ import annotations
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from relay.clock import SimClock
from relay.io_image import IOImage


@dataclass(frozen=True)
class Receipt:
    """What a message carried, recorded where it was delivered.

    Attribution needs message identity, not just a counter: `sender` says whose
    per-sender seq space `seq` belongs to, and `value` is what the message
    actually delivered — neither can be re-derived from the trace's merged
    signal view without guessing. `sender` is None for plant-routed and
    strategy-routed messages, which have no PLC sender and are therefore not
    attributable.
    """

    sender: str | None
    seq: int
    value: Any


@dataclass(frozen=True)
class SendRecord:
    """What a message carried, recorded where it was emitted.

    The send counter alone cannot answer "when did this PLC first emit a
    truthy value" — a producer that sends every scan carries `False` long
    before the real event, and a count rising from 10 to 11 says a message
    left, not what it said. Timing claims need the value at the sending end:
    PRECEDES reads its first endpoint here rather than walking back from a
    receipt, so a send's timestamp does not depend on who consumed it, or on
    whether anyone did.

    `count` is this sender's cumulative per-key send count, the same number
    receipts carry as `seq`.
    """

    count: int
    value: Any


@dataclass(frozen=True)
class ScanRecord:
    plc_id: str
    clock: SimClock
    io: IOImage
    outputs: IOImage
    sends: Mapping[str, SendRecord] = field(default_factory=dict)
    recvs: Mapping[str, Receipt] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("sends", "recvs"):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))


@dataclass
class TraceLog:
    records: list[ScanRecord] = field(default_factory=list)

    def record(self, scan: ScanRecord) -> None:
        self.records.append(scan)

    def for_plc(self, plc_id: str) -> list[ScanRecord]:
        return [r for r in self.records if r.plc_id == plc_id]

    def values_at(self, plc_id: str, key: str) -> list[tuple[float, Any]]:
        return [
            (r.clock.elapsed_ms, r.outputs.get(key))
            for r in self.for_plc(plc_id)
        ]
