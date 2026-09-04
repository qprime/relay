from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from relay.clock import SimClock
from relay.io_image import IOImage
from relay.runtime.comm import CommBuffer
from relay.st.interpreter import STContext, execute
from relay.strategies.st_syntax import SCRATCH_PREFIX, SEND_PREFIX


@dataclass
class FunctionBlock:
    source: str
    plc_ids: tuple[str, ...] = ()
    _ctx: STContext = field(default_factory=STContext, init=False)

    def scan(
        self,
        io: IOImage,
        comm: CommBuffer,
        clock: SimClock,
        dt_ms: float,
    ) -> tuple[IOImage, list[tuple[str, Any]]]:
        for key, value in io.values.items():
            self._ctx.set(key, value)
        for key, value in comm.pending.items():
            self._ctx.set(key, value)

        execute(self.source, self._ctx, dt_ms)

        outputs = IOImage.empty()
        outgoing: list[tuple[str, Any]] = []
        for key in self._ctx.assigned:
            value = self._ctx.variables[key]
            if key.startswith(SCRATCH_PREFIX):
                continue
            if key.startswith(SEND_PREFIX):
                outgoing.append((self._parse_send(key), value))
            else:
                outputs = outputs.with_value(key, value)

        return outputs, outgoing

    def _parse_send(self, name: str) -> str:
        signal = name[len(SEND_PREFIX) :]
        if not signal:
            raise ValueError(f"_send_* assignment {name!r} has an empty signal suffix")
        return signal
