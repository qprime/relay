from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from relay.runtime.clock import SimClock

if TYPE_CHECKING:
    from relay.runtime.plc import IOImage


@dataclass(frozen=True)
class ScanRecord:
    plc_id: str
    clock: SimClock
    io: "IOImage"
    outputs: "IOImage"


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
