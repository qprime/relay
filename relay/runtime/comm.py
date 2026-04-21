from __future__ import annotations
import asyncio
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class CommBuffer:
    pending: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.pending, MappingProxyType):
            object.__setattr__(self, "pending", MappingProxyType(dict(self.pending)))

    @staticmethod
    def empty() -> CommBuffer:
        return CommBuffer(pending={})

    def with_value(self, key: str, value: Any) -> CommBuffer:
        return CommBuffer(pending={**self.pending, key: value})

    def promote(self) -> tuple[Mapping[str, Any], CommBuffer]:
        return MappingProxyType(dict(self.pending)), CommBuffer.empty()


class CommBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[tuple[str, Any]]] = {}

    def register(self, plc_id: str) -> None:
        self._queues[plc_id] = asyncio.Queue()

    async def send(self, to_plc: str, key: str, value: Any) -> None:
        await self._queues[to_plc].put((key, value))

    async def drain(self, plc_id: str) -> CommBuffer:
        buf = CommBuffer.empty()
        queue = self._queues[plc_id]
        while not queue.empty():
            key, value = await queue.get()
            buf = buf.with_value(key, value)
        return buf
