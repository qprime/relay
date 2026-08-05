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
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[tuple[str, Any, str | None, int]]] = {}

    def register(self, plc_id: str) -> None:
        self._queues[plc_id] = asyncio.Queue()

    async def send(
        self, to_plc: str, key: str, value: Any, sender: str | None, seq: int
    ) -> None:
        await self._queues[to_plc].put((key, value, sender, seq))

    async def drain(self, plc_id: str) -> CommBuffer:
        buf = CommBuffer.empty()
        queue = self._queues[plc_id]
        while not queue.empty():
            key, value, sender, seq = await queue.get()
            buf = buf.with_value(key, value, sender, seq)
        return buf
