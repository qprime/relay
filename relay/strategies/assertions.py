from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal


EVENTUALLY_RE = re.compile(
    r"EVENTUALLY\((\w+),\s*within:\s*(\d+(?:\.\d+)?)\s*ms\)", re.IGNORECASE
)
PRECEDES_RE = re.compile(
    r"PRECEDES\((\w+),\s*(\w+),\s*within:\s*(\d+(?:\.\d+)?)\s*ms\)", re.IGNORECASE
)


@dataclass(frozen=True)
class ParsedAssertion:
    form: Literal["EVENTUALLY", "PRECEDES"]
    signals: tuple[str, ...]
    within_ms: float | None = None


def parse_assertion(s: str) -> ParsedAssertion | None:
    s = s.strip()
    m = EVENTUALLY_RE.match(s)
    if m:
        return ParsedAssertion(
            form="EVENTUALLY",
            signals=(m.group(1),),
            within_ms=float(m.group(2)),
        )
    m = PRECEDES_RE.match(s)
    if m:
        return ParsedAssertion(
            form="PRECEDES",
            signals=(m.group(1), m.group(2)),
            within_ms=float(m.group(3)),
        )
    return None
