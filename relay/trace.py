from __future__ import annotations
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from relay.clock import SimClock
from relay.io_image import IOImage


@dataclass(frozen=True)
class ScanRecord:
    plc_id: str
    clock: SimClock
    io: IOImage
    outputs: IOImage
    sends: Mapping[str, int] = field(default_factory=dict)
    recvs: Mapping[str, int] = field(default_factory=dict)

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
