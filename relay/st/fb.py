from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from relay.st.interpreter import STContext, execute
from relay.runtime.clock import SimClock
from relay.runtime.comm import CommBuffer
from relay.runtime.plc import IOImage


@dataclass
class FunctionBlock:
    source: str
    _ctx: STContext = field(default_factory=STContext, init=False)

    def scan(
        self,
        io: IOImage,
        comm: CommBuffer,
        clock: SimClock,
        dt_ms: float,
    ) -> tuple[IOImage, list[tuple[str, str, Any]]]:
        for key, value in io.values.items():
            self._ctx.set(key, value)
        for key, value in comm.pending.items():
            self._ctx.set(key, value)

        execute(self.source, self._ctx, dt_ms)

        outputs = IOImage.empty()
        for key in self._ctx.assigned:
            outputs = outputs.with_value(key, self._ctx.variables[key])

        return outputs, []
