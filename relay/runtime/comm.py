from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from relay.trace import Receipt


@dataclass(frozen=True)
class CommBuffer:
    pending: Mapping[str, Any]
    receipts: Mapping[str, Receipt] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("pending", "receipts"):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))

    @staticmethod
    def empty() -> CommBuffer:
        return CommBuffer(pending={})

    def with_value(
        self, key: str, value: Any, sender: str | None, seq: int
    ) -> CommBuffer:
        return CommBuffer(
            pending={**self.pending, key: value},
            receipts={
                **self.receipts,
                key: Receipt(sender=sender, seq=seq, value=value),
            },
        )

    def promote(self) -> Mapping[str, Any]:
        return MappingProxyType(dict(self.pending))


class CommBus:
    """Inter-PLC message transport that charges one consumer scan of latency.

    A message becomes visible at the consumer's first scan top whose SimClock
    time is strictly later than the sending scan's. Delivery is paced by the
    consumer's sampling, so the cost is up to one *consumer* scan period —
    not one sender period, and not one global period. That phrasing is what
    lets the rule survive independent per-PLC periods: it references only the
    sender's stamp and the consumer's scan top, never a shared iteration.

    Without this, a PLC's in-scan `bus.send` was readable by any consumer whose
    coroutine had not yet run in the same harness iteration, so latency was
    zero along declaration order and one scan against it — ordering-dependent
    timing that reversing `System.plcs` would change.

    Plant- and harness-routed messages carry no stamp and are always eligible.
    A plant route models a sensor wired to the PLC's input terminals and
    sampled at scan top, not a network. Their `as_key` names are disjoint from
    tag names by the manual's collision rules, so an unstamped message
    overtaking a queued stamped one is unobservable per key.
    """

    def __init__(self) -> None:
        self._queues: dict[
            str, asyncio.Queue[tuple[str, Any, str | None, int, float | None]]
        ] = {}

    def register(self, plc_id: str) -> None:
        self._queues[plc_id] = asyncio.Queue()

    async def send(
        self,
        to_plc: str,
        key: str,
        value: Any,
        sender: str | None,
        seq: int,
        sent_elapsed_ms: float | None = None,
    ) -> None:
        await self._queues[to_plc].put((key, value, sender, seq, sent_elapsed_ms))

    async def drain(self, plc_id: str, before_elapsed_ms: float) -> CommBuffer:
        buf = CommBuffer.empty()
        queue = self._queues[plc_id]
        deferred: list[tuple[str, Any, str | None, int, float | None]] = []
        while not queue.empty():
            entry = await queue.get()
            sent_elapsed_ms = entry[4]
            if sent_elapsed_ms is not None and sent_elapsed_ms >= before_elapsed_ms:
                deferred.append(entry)
                continue
            key, value, sender, seq, _ = entry
            buf = buf.with_value(key, value, sender, seq)
        for entry in deferred:
            await queue.put(entry)
        return buf
