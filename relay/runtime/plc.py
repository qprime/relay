from __future__ import annotations
import asyncio
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from relay.runtime.clock import SimClock
from relay.runtime.comm import CommBuffer, CommBus
from relay.verify.trace import TraceLog, ScanRecord


@dataclass(frozen=True)
class IOImage:
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.values, MappingProxyType):
            object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @staticmethod
    def empty() -> IOImage:
        return IOImage(values={})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def with_value(self, key: str, value: Any) -> IOImage:
        return IOImage(values={**self.values, key: value})


FBExecutor = Callable[
    [IOImage, CommBuffer, SimClock, float],
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
        scan_done: asyncio.Queue[None],
    ) -> None:
        io = IOImage.empty()

        for _ in range(max_scans):
            clock = await clock_source.get()

            comm = await bus.drain(self.plc_id)
            promoted, _ = comm.promote()
            for key, value in promoted.items():
                io = io.with_value(key, value)

            snapshot = io

            outputs, outgoing = self.executor(snapshot, comm, clock, self.scan_period_ms)

            for target_plc, key, value in outgoing:
                await bus.send(target_plc, key, value)

            for key, value in outputs.values.items():
                io = io.with_value(key, value)

            trace.record(ScanRecord(plc_id=self.plc_id, clock=clock, io=snapshot, outputs=outputs))
            await scan_done.put(None)
