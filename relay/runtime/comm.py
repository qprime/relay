from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType
from typing import Any, Mapping

from relay.trace import Receipt
from relay.clock import SimClock
from relay.runtime.can import CanScheduler, CompletedCanFrame
from relay.strategies.comm import CanBinding, CommSignal, CommTransportConfig


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

    def with_value(self, key: str, value: Any, sender: str | None, seq: int) -> CommBuffer:
        return CommBuffer(
            pending={**self.pending, key: value},
            receipts={
                **self.receipts,
                key: Receipt(sender=sender, seq=seq, value=value),
            },
        )

    def promote(self) -> Mapping[str, Any]:
        return MappingProxyType(dict(self.pending))

    def merged(self, other: CommBuffer) -> CommBuffer:
        return CommBuffer(
            pending={**self.pending, **other.pending},
            receipts={**self.receipts, **other.receipts},
        )


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
        self._queues: dict[str, asyncio.Queue[tuple[str, Any, str | None, int, float | None]]] = {}

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


@dataclass(frozen=True)
class OutgoingMessage:
    signal: str
    value: Any
    producer: str
    seq: int
    send_time: SimClock


class InProcessTransport:
    def __init__(self, bus: CommBus, signals: tuple[CommSignal, ...]) -> None:
        self._bus = bus
        self._signals = {signal.name: signal for signal in signals}

    async def emit(self, message: OutgoingMessage):
        try:
            signal = self._signals[message.signal]
        except KeyError:
            raise ValueError(
                f"_send_* signal {message.signal!r} is not a declared comm signal"
            ) from None
        for consumer in signal.consumed_by:
            await self._bus.send(
                consumer,
                message.signal,
                message.value,
                message.producer,
                message.seq,
                message.send_time.elapsed_ms,
            )
        return None

    def settle(self, clock: SimClock) -> tuple[CompletedCanFrame, ...]:
        return ()

    async def poll(self, plc_id: str, clock: SimClock) -> CommBuffer:
        return CommBuffer.empty()


class CanTransport:
    def __init__(
        self, signals: tuple[CommSignal, ...], bindings: dict[str, CanBinding], baud_rate: int
    ) -> None:
        self._signals = {signal.name: signal for signal in signals}
        self._bindings = bindings
        self._scheduler = CanScheduler(baud_rate)
        self._completed: dict[str, list[CompletedCanFrame]] = {}

    async def emit(self, message: OutgoingMessage):
        binding = self._bindings[message.signal]
        frame = self._scheduler.enqueue(
            message.signal,
            binding.can_id,
            message.value,
            message.producer,
            message.seq,
            message.send_time.elapsed_ms,
        )
        return {"can_id": binding.can_id, "frame_bits": frame.bit_count}

    def settle(self, clock: SimClock) -> tuple[CompletedCanFrame, ...]:
        completed_frames = self._scheduler.settle(clock.elapsed_ms)
        for completed in completed_frames:
            for consumer in self._signals[completed.frame.signal].consumed_by:
                self._completed.setdefault(consumer, []).append(completed)
        return completed_frames

    async def poll(self, plc_id: str, clock: SimClock) -> CommBuffer:
        frames = self._completed.pop(plc_id, [])
        latest: dict[str, CompletedCanFrame] = {}
        for completed in frames:
            if completed.completion_ms <= clock.elapsed_ms and completed.frame.ready_ms < Fraction(
                str(clock.elapsed_ms)
            ):
                latest[completed.frame.signal] = completed
        buf = CommBuffer.empty()
        for signal, completed in latest.items():
            frame = completed.frame
            buf = buf.with_value(signal, frame.value, frame.sender, frame.seq)
        return buf


def build_transport(
    config: CommTransportConfig,
    bus: CommBus,
    signals: tuple[CommSignal, ...],
    bindings: dict[str, CanBinding],
):
    factories = {
        "in_process": lambda: InProcessTransport(bus, signals),
        "modbus": lambda: InProcessTransport(bus, signals),
        "can": lambda: CanTransport(signals, bindings, config.baud_rate or 0),
    }
    try:
        return factories[config.kind]()
    except KeyError:
        raise ValueError(f"unknown comm transport {config.kind!r}") from None
