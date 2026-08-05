from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class CommBuffer:
    pending: Mapping[str, Any]
    counters: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("pending", "counters"):
            value = getattr(self, name)
            if not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))

    @staticmethod
    def empty() -> CommBuffer:
        return CommBuffer(pending={})

    def with_value(self, key: str, value: Any, seq: int) -> CommBuffer:
        return CommBuffer(
            pending={**self.pending, key: value},
            counters={**self.counters, key: seq},
        )

    def promote(self) -> Mapping[str, Any]:
        return MappingProxyType(dict(self.pending))


class CommBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[tuple[str, Any, int]]] = {}

    def register(self, plc_id: str) -> None:
        self._queues[plc_id] = asyncio.Queue()

    async def send(self, to_plc: str, key: str, value: Any, seq: int) -> None:
        await self._queues[to_plc].put((key, value, seq))

    async def drain(self, plc_id: str) -> CommBuffer:
        buf = CommBuffer.empty()
        queue = self._queues[plc_id]
        while not queue.empty():
            key, value, seq = await queue.get()
            buf = buf.with_value(key, value, seq)
        return buf
