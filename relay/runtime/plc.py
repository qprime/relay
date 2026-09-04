from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from relay.clock import DEFAULT_SCAN_PERIOD_MS, SimClock
from relay.io_image import IOImage
from relay.runtime.comm import CommBuffer, CommBus, OutgoingMessage
from relay.trace import ScanRecord, SendRecord, TraceLog


FBExecutor = Callable[
    [IOImage, CommBuffer, SimClock, float],
    tuple[IOImage, list[tuple[str, Any]]],
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
        transport: Any,
        trace: TraceLog,
        max_scans: int,
        scan_done: asyncio.Queue[None],
    ) -> None:
        io = IOImage.empty()
        send_counts: dict[str, int] = {}

        for _ in range(max_scans):
            clock = await clock_source.get()

            comm = (await bus.drain(self.plc_id, clock.elapsed_ms)).merged(
                await transport.poll(self.plc_id, clock)
            )
            for key, value in comm.promote().items():
                io = io.with_value(key, value)

            snapshot = io

            outputs, outgoing = self.executor(snapshot, comm, clock, self.scan_period_ms)

            sends: dict[str, SendRecord] = {}
            for key, value in outgoing:
                seq = send_counts.get(key, 0) + 1
                send_counts[key] = seq
                metadata = await transport.emit(
                    OutgoingMessage(key, value, self.plc_id, seq, clock)
                )
                sends[key] = SendRecord(count=seq, value=value, **(metadata or {}))

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
