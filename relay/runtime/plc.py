from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from relay.clock import DEFAULT_SCAN_PERIOD_MS, SimClock
from relay.io_image import IOImage
from relay.runtime.comm import CommBuffer, CommBus
from relay.trace import ScanRecord, SendRecord, TraceLog


FBExecutor = Callable[
    [IOImage, CommBuffer, SimClock, float],
    tuple[IOImage, list[tuple[str, str, Any]]],
]


@dataclass
class PLCCoroutine:
    plc_id: str
    executor: FBExecutor
    scan_period_ms: float = DEFAULT_SCAN_PERIOD_MS

    async def run(
        self,
        clock_source: asyncio.Queue[SimClock],
        bus: CommBus,
        trace: TraceLog,
        max_scans: int,
        scan_done: asyncio.Queue[None],
    ) -> None:
        io = IOImage.empty()
        send_counts: dict[str, int] = {}

        for _ in range(max_scans):
            clock = await clock_source.get()

            comm = await bus.drain(self.plc_id)
            for key, value in comm.promote().items():
                io = io.with_value(key, value)

            snapshot = io

            outputs, outgoing = self.executor(snapshot, comm, clock, self.scan_period_ms)

            sends: dict[str, SendRecord] = {}
            for target_plc, key, value in outgoing:
                seq = send_counts.get(key, 0) + 1
                send_counts[key] = seq
                sends[key] = SendRecord(count=seq, value=value)
                await bus.send(target_plc, key, value, self.plc_id, seq)

            for key, value in outputs.values.items():
                io = io.with_value(key, value)

            trace.record(
                ScanRecord(
                    plc_id=self.plc_id,
                    clock=clock,
                    io=snapshot,
                    outputs=outputs,
                    sends=sends,
                    recvs=dict(comm.receipts),
                )
            )
            await scan_done.put(None)
