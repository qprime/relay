from __future__ import annotations
from dataclasses import dataclass


DEFAULT_SCAN_PERIOD_MS = 10.0


@dataclass(frozen=True)
class SimClock:
    tick: int
    elapsed_ms: float

    def advance(self, scan_period_ms: float) -> SimClock:
        return SimClock(tick=self.tick + 1, elapsed_ms=self.elapsed_ms + scan_period_ms)

    @staticmethod
    def zero() -> SimClock:
        return SimClock(tick=0, elapsed_ms=0.0)
