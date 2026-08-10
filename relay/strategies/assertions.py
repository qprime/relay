from __future__ import annotations
import math
import re
from dataclasses import dataclass
from typing import Literal

from relay.strategies.comm import CommSignal


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


def causes_issues(
    assertions: list, signals: tuple[CommSignal, ...]
) -> list[str]:
    """Rules CAUSES needs beyond grammar: the cause must be a declared comm
    signal, and a signal cannot cause itself.

    Attribution is only possible for messages carrying a sender and a sequence
    number, which is what comm routing provides. Plant-routed signals reach a
    PLC with no attributable sender, so a CAUSES naming one can never pass — a
    spec-load failure beats a verification-time failure that reads as a
    behavior bug.

    Takes the strategy's projection rather than a raw comm block so the rule is
    strategy-neutral: whichever idiom declared the signal, only its projected
    name matters here. Lives on the leaf so `spec.load_spec` and
    `generator.validate_spec` enforce identical rules; a check on only one path
    is a hole in whichever path the LLM writes through.
    """
    signal_names = {s.name for s in signals}
    issues: list[str] = []
    for assertion in assertions or []:
        parsed = parse_assertion(assertion) if isinstance(assertion, str) else None
        if parsed is None or parsed.form != "CAUSES":
            continue
        cause, effect = parsed.signals
        if cause == effect:
            issues.append(
                f"{assertion!r} names {cause!r} as both cause and effect; "
                "a signal cannot cause itself"
            )
            continue
        if cause not in signal_names:
            known = ", ".join(sorted(signal_names)) or "(none declared)"
            issues.append(
                f"{assertion!r} names {cause!r} as the cause, which is not a "
                "declared comm signal; only comm messages carry the sender "
                f"and sequence attribution needs. Declared signals: {known}"
            )
    return issues


def _finite_budget(text: str) -> float | None:
    """A budget too large for a double is not a budget.

    The grammar admits any digit string, so `within: <400 digits>ms` reaches
    here. `float()` yields `inf`, which would make EVENTUALLY pass for any
    signal that ever became true — a budget that cannot be exceeded is not one.
    Rejecting at the grammar makes it an unrecognized form on both sides, so
    `generator.validate_spec` fails the spec instead of the verifier quietly
    passing it.
    """
    value = float(text)
    return value if math.isfinite(value) else None


def parse_assertion(s: str) -> ParsedAssertion | None:
    s = s.strip()
    m = EVENTUALLY_RE.fullmatch(s)
    if m:
        budget = _finite_budget(m.group(2))
        return (
            None
            if budget is None
            else ParsedAssertion(
                form="EVENTUALLY", signals=(m.group(1),), within_ms=budget
            )
        )
    m = PRECEDES_RE.fullmatch(s)
    if m:
        budget = _finite_budget(m.group(3))
        return (
            None
            if budget is None
            else ParsedAssertion(
                form="PRECEDES",
                signals=(m.group(1), m.group(2)),
                within_ms=budget,
            )
        )
    m = CAUSES_RE.fullmatch(s)
    if m:
        return ParsedAssertion(form="CAUSES", signals=(m.group(1), m.group(2)))
    return None
