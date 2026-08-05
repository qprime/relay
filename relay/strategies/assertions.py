from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal


EVENTUALLY_RE = re.compile(
    r"EVENTUALLY\(\s*(\w+)\s*,\s*within:\s*(\d+(?:\.\d+)?)\s*ms\s*\)", re.IGNORECASE
)
PRECEDES_RE = re.compile(
    r"PRECEDES\(\s*(\w+)\s*,\s*(\w+)\s*,\s*within:\s*(\d+(?:\.\d+)?)\s*ms\s*\)",
    re.IGNORECASE,
)
CAUSES_RE = re.compile(r"CAUSES\(\s*(\w+)\s*,\s*(\w+)\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedAssertion:
    form: Literal["EVENTUALLY", "PRECEDES", "CAUSES"]
    signals: tuple[str, ...]
    within_ms: float | None = None


def parse_assertion(s: str) -> ParsedAssertion | None:
    s = s.strip()
    m = EVENTUALLY_RE.fullmatch(s)
    if m:
        return ParsedAssertion(
            form="EVENTUALLY",
            signals=(m.group(1),),
            within_ms=float(m.group(2)),
        )
    m = PRECEDES_RE.fullmatch(s)
    if m:
        return ParsedAssertion(
            form="PRECEDES",
            signals=(m.group(1), m.group(2)),
            within_ms=float(m.group(3)),
        )
    m = CAUSES_RE.fullmatch(s)
    if m:
        return ParsedAssertion(form="CAUSES", signals=(m.group(1), m.group(2)))
    return None
