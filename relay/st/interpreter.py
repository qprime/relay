from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class STContext:
    variables: dict[str, Any] = field(default_factory=dict)
    timers: dict[str, _Timer] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def get(self, name: str) -> Any:
        return self.variables.get(name, False)

    def set(self, name: str, value: Any) -> None:
        self.variables[name] = value


@dataclass
class _Timer:
    preset_ms: float
    accumulated_ms: float = 0.0
    running: bool = False
    done: bool = False

    def tick(self, dt_ms: float, enable: bool) -> None:
        if enable:
            self.running = True
            self.accumulated_ms = min(self.accumulated_ms + dt_ms, self.preset_ms)
            self.done = self.accumulated_ms >= self.preset_ms
        else:
            self.running = False
            self.accumulated_ms = 0.0
            self.done = False


_TON_RE = re.compile(
    r"(\w+)\s*\(\s*IN\s*:=\s*(.+?)\s*,\s*PT\s*:=\s*T#(\d+(?:\.\d+)?)ms\s*\)", re.IGNORECASE
)
_IF_RE = re.compile(r"IF\s+(.+?)\s+THEN", re.IGNORECASE)
_ASSIGN_RE = re.compile(r"(\w+)\s*:=\s*(.+)")
_ENDIF_RE = re.compile(r"END_IF\s*;?", re.IGNORECASE)


def execute(source: str, ctx: STContext, dt_ms: float) -> None:
    ctx.elapsed_ms += dt_ms
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    _exec_block(lines, 0, len(lines), ctx, dt_ms)


def _exec_block(lines: list[str], start: int, end: int, ctx: STContext, dt_ms: float) -> None:
    i = start
    while i < end:
        line = lines[i]

        ton_m = _TON_RE.match(line)
        if ton_m:
            name, enable_expr, preset = ton_m.group(1), ton_m.group(2), ton_m.group(3)
            if name not in ctx.timers:
                ctx.timers[name] = _Timer(preset_ms=float(preset))
            enable = _eval_expr(enable_expr, ctx)
            ctx.timers[name].tick(dt_ms, bool(enable))
            i += 1
            continue

        if_m = _IF_RE.match(line)
        if if_m:
            condition = _eval_expr(if_m.group(1), ctx)
            body_start = i + 1
            depth = 1
            j = body_start
            while j < end and depth > 0:
                if _IF_RE.match(lines[j]):
                    depth += 1
                if _ENDIF_RE.match(lines[j]):
                    depth -= 1
                if depth > 0:
                    j += 1
                else:
                    break
            if condition:
                _exec_block(lines, body_start, j, ctx, dt_ms)
            i = j + 1
            continue

        assign_m = _ASSIGN_RE.match(line.rstrip(";"))
        if assign_m:
            name, expr = assign_m.group(1), assign_m.group(2)
            ctx.set(name, _eval_expr(expr, ctx))
            i += 1
            continue

        i += 1


def _eval_expr(expr: str, ctx: STContext) -> Any:
    expr = expr.strip()

    if expr.upper() == "TRUE":
        return True
    if expr.upper() == "FALSE":
        return False

    if re.match(r"^-?\d+(\.\d+)?$", expr):
        return float(expr) if "." in expr else int(expr)

    expr_upper = expr.upper()
    if expr_upper.startswith("NOT "):
        return not _eval_expr(expr[4:], ctx)

    if " AND " in expr_upper:
        parts = re.split(r"\bAND\b", expr, flags=re.IGNORECASE)
        return all(_eval_expr(p, ctx) for p in parts)
    if " OR " in expr_upper:
        parts = re.split(r"\bOR\b", expr, flags=re.IGNORECASE)
        return any(_eval_expr(p, ctx) for p in parts)

    for op in [">=", "<=", "<>", ">", "<", "="]:
        if op in expr:
            idx = expr.index(op)
            lhs = _eval_expr(expr[:idx], ctx)
            rhs = _eval_expr(expr[idx + len(op):], ctx)
            return _compare(lhs, rhs, op)

    for op in ["+", "-", "*", "/"]:
        if op in expr:
            parts = expr.split(op, 1)
            lhs = _eval_expr(parts[0], ctx)
            rhs = _eval_expr(parts[1], ctx)
            return _arith(lhs, rhs, op)

    dot_m = re.match(r"(\w+)\.(\w+)", expr)
    if dot_m:
        obj_name, attr = dot_m.group(1), dot_m.group(2)
        timer = ctx.timers.get(obj_name)
        if timer is not None:
            return getattr(timer, attr.lower(), False)
        return False

    return ctx.get(expr)


def _compare(lhs: Any, rhs: Any, op: str) -> bool:
    ops = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
           "<>": lambda a, b: a != b, ">": lambda a, b: a > b,
           "<": lambda a, b: a < b, "=": lambda a, b: a == b}
    return ops[op](lhs, rhs)


def _arith(lhs: Any, rhs: Any, op: str) -> Any:
    ops = {"+": lambda a, b: a + b, "-": lambda a, b: a - b,
           "*": lambda a, b: a * b, "/": lambda a, b: a / b}
    return ops[op](lhs, rhs)
