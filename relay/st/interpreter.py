from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any

from relay.strategies.st_syntax import _ASSIGN_RE, _ENDIF_RE, _IF_RE, _TON_RE


@dataclass
class STContext:
    variables: dict[str, Any] = field(default_factory=dict)
    timers: dict[str, _Timer] = field(default_factory=dict)
    assigned: set[str] = field(default_factory=set)

    def get(self, name: str) -> Any:
        return self.variables.get(name, False)

    def set(self, name: str, value: Any) -> None:
        self.variables[name] = value

    def assign(self, name: str, value: Any) -> None:
        self.variables[name] = value
        self.assigned.add(name)


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


def execute(source: str, ctx: STContext, dt_ms: float) -> None:
    ctx.assigned.clear()
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
            ctx.assign(name, _eval_expr(expr, ctx))
            i += 1
            continue

        i += 1


_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<ident>[A-Za-z_]\w*(?:\.\w+)?)"
    r"|(?P<op><=|>=|<>|:=|=|<|>|\+|-|\*|/|\(|\))"
    r")"
)


def _tokenize(expr: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos].isspace():
                pos += 1
                continue
            raise ValueError(f"unexpected character {expr[pos]!r} at {pos} in {expr!r}")
        pos = m.end()
        if m.group("num") is not None:
            tokens.append(("num", m.group("num")))
        elif m.group("ident") is not None:
            ident = m.group("ident")
            upper = ident.upper()
            if upper in {"AND", "OR", "NOT", "TRUE", "FALSE"}:
                tokens.append(("kw", upper))
            else:
                tokens.append(("ident", ident))
        else:
            tokens.append(("op", m.group("op")))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], ctx: STContext) -> None:
        self._tokens = tokens
        self._pos = 0
        self._ctx = ctx

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _consume(self) -> tuple[str, str]:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def parse(self) -> Any:
        value = self._parse_or()
        if self._pos != len(self._tokens):
            raise ValueError(f"unexpected token {self._tokens[self._pos]!r}")
        return value

    def _parse_or(self) -> Any:
        left = self._parse_and()
        while self._peek() == ("kw", "OR"):
            self._consume()
            right = self._parse_and()
            left = bool(left) or bool(right)
        return left

    def _parse_and(self) -> Any:
        left = self._parse_not()
        while self._peek() == ("kw", "AND"):
            self._consume()
            right = self._parse_not()
            left = bool(left) and bool(right)
        return left

    def _parse_not(self) -> Any:
        if self._peek() == ("kw", "NOT"):
            self._consume()
            return not bool(self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Any:
        left = self._parse_additive()
        tok = self._peek()
        if tok is not None and tok[0] == "op" and tok[1] in {"=", "<>", "<", "<=", ">", ">="}:
            self._consume()
            right = self._parse_additive()
            return _compare(left, right, tok[1])
        return left

    def _parse_additive(self) -> Any:
        left = self._parse_multiplicative()
        while True:
            tok = self._peek()
            if tok is None or tok[0] != "op" or tok[1] not in {"+", "-"}:
                return left
            self._consume()
            right = self._parse_multiplicative()
            left = _arith(left, right, tok[1])

    def _parse_multiplicative(self) -> Any:
        left = self._parse_unary()
        while True:
            tok = self._peek()
            if tok is None or tok[0] != "op" or tok[1] not in {"*", "/"}:
                return left
            self._consume()
            right = self._parse_unary()
            left = _arith(left, right, tok[1])

    def _parse_unary(self) -> Any:
        tok = self._peek()
        if tok == ("op", "-"):
            self._consume()
            return -self._parse_unary()
        if tok == ("op", "+"):
            self._consume()
            return self._parse_unary()
        return self._parse_primary()

    def _parse_primary(self) -> Any:
        tok = self._consume()
        kind, text = tok
        if kind == "op" and text == "(":
            value = self._parse_or()
            closing = self._consume()
            if closing != ("op", ")"):
                raise ValueError(f"expected ')' got {closing!r}")
            return value
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "kw" and text == "TRUE":
            return True
        if kind == "kw" and text == "FALSE":
            return False
        if kind == "ident":
            if "." in text:
                obj_name, attr = text.split(".", 1)
                timer = self._ctx.timers.get(obj_name)
                if timer is not None:
                    return getattr(timer, attr.lower(), False)
                return False
            return self._ctx.get(text)
        raise ValueError(f"unexpected token {tok!r}")


def _eval_expr(expr: str, ctx: STContext) -> Any:
    tokens = _tokenize(expr)
    return _Parser(tokens, ctx).parse()


def _compare(lhs: Any, rhs: Any, op: str) -> bool:
    ops = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
           "<>": lambda a, b: a != b, ">": lambda a, b: a > b,
           "<": lambda a, b: a < b, "=": lambda a, b: a == b}
    return ops[op](lhs, rhs)


def _arith(lhs: Any, rhs: Any, op: str) -> Any:
    ops = {"+": lambda a, b: a + b, "-": lambda a, b: a - b,
           "*": lambda a, b: a * b, "/": lambda a, b: a / b}
    return ops[op](lhs, rhs)
