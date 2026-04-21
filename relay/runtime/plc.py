from __future__ import annotations
import asyncio
from dataclasses import dataclass, replace
from typing import Any, Callable

from relay.runtime.clock import SimClock
from relay.runtime.comm import CommBuffer, CommBus
from relay.verify.trace import TraceLog, ScanRecord


@dataclass(frozen=True)
class IOImage:
    values: dict[str, Any]

    @staticmethod
    def empty() -> IOImage:
        return IOImage(values={})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def with_value(self, key: str, value: Any) -> IOImage:
        return replace(self, values={**self.values, key: value})


# Returns updated IO image + list of (target_plc, key, value) outgoing messages
FBExecutor = Callable[
    [IOImage, CommBuffer, SimClock],
    tuple[IOImage, list[tuple[str, str, Any]]],
]


@dataclass
class PLCCoroutine:
    plc_id: str
    executor: FBExecutor
    scan_period_ms: float = 10.0

    async def run(
        self,
        clock_source: asyncio.Queue[SimClock],
        bus: CommBus,
        trace: TraceLog,
        max_scans: int,
    ) -> None:
        io = IOImage.empty()

        for _ in range(max_scans):
            clock = await clock_source.get()

            comm = await bus.drain(self.plc_id)
            promoted, _ = comm.promote()
            for key, value in promoted.items():
                io = io.with_value(key, value)

            snapshot = io

            new_io, outgoing = self.executor(snapshot, comm, clock)

            for target_plc, key, value in outgoing:
                await bus.send(target_plc, key, value)

            io = new_io
            trace.record(ScanRecord(plc_id=self.plc_id, clock=clock, io=snapshot, outputs=new_io))
