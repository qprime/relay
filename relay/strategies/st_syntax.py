from __future__ import annotations
import re


SEND_PREFIX = "_send_"
SCRATCH_PREFIX = "_scratch_"


_TON_RE = re.compile(
    r"(\w+)\s*\(\s*IN\s*:=\s*(.+?)\s*,\s*PT\s*:=\s*T#(\d+(?:\.\d+)?)ms\s*\)", re.IGNORECASE
)
_IF_RE = re.compile(r"IF\s+(.+?)\s+THEN", re.IGNORECASE)
_ENDIF_RE = re.compile(r"END_IF\s*;?", re.IGNORECASE)
_ASSIGN_RE = re.compile(r"(\w+)\s*:=\s*(.+)")


def _line_kind(line: str) -> str | None:
    stripped = line.strip().rstrip(";")
    if not stripped:
        return "blank"
    if _TON_RE.match(stripped):
        return "ton"
    if _IF_RE.match(stripped):
        return "if"
    if _ENDIF_RE.match(stripped):
        return "endif"
    if _ASSIGN_RE.match(stripped):
        return "assign"
    return None


def static_parse_errors(source: str) -> list[str]:
    errors: list[str] = []
    depth = 0
    for lineno, raw_line in enumerate(source.splitlines(), 1):
        kind = _line_kind(raw_line)
        if kind is None:
            errors.append(f"line {lineno}: unrecognized ST syntax: {raw_line.strip()!r}")
            continue
        if kind == "if":
            depth += 1
        elif kind == "endif":
            depth -= 1
            if depth < 0:
                errors.append(f"line {lineno}: END_IF without matching IF")
                depth = 0
    if depth != 0:
        errors.append(f"unbalanced IF blocks (depth={depth} at end of source)")
    return errors


def assignment_lhs(source: str) -> list[str]:
    names: list[str] = []
    for line in source.splitlines():
        stripped = line.strip().rstrip(";")
        m = _ASSIGN_RE.match(stripped)
        if not m:
            continue
        if _TON_RE.match(stripped):
            continue
        names.append(m.group(1))
    return names


def ton_presets(source: str) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    for line in source.splitlines():
        m = _TON_RE.search(line.strip())
        if m:
            found.append((m.group(1), float(m.group(3))))
    return found
