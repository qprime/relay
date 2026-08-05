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


def causes_issues(assertions: list, comm_block: dict) -> list[str]:
    """Rules CAUSES needs beyond grammar: the cause must be a declared tag, and
    a signal cannot cause itself.

    Attribution is only possible for messages carrying a sender and a sequence
    number, which is what `Comm.tags` routing provides. Plant-routed signals
    reach a PLC with no attributable sender, so a CAUSES naming one can never
    pass — a spec-load failure beats a verification-time failure that reads as
    a behavior bug.

    Lives on the leaf so `spec.load_spec` and `generator.validate_spec` enforce
    identical rules; a check on only one path is a hole in whichever path the
    LLM writes through.
    """
    tag_names = {
        tag["name"]
        for tag in (comm_block.get("tags") or [])
        if isinstance(tag, dict) and isinstance(tag.get("name"), str)
    }
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
        if cause not in tag_names:
            known = ", ".join(sorted(tag_names)) or "(none declared)"
            issues.append(
                f"{assertion!r} names {cause!r} as the cause, which is not a "
                "declared Comm.tags entry; only tag messages carry the sender "
                f"and sequence attribution needs. Declared tags: {known}"
            )
    return issues


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
