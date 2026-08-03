from __future__ import annotations

import pytest

from relay.st.interpreter import STContext, execute


def _run(source: str, ctx: STContext, dt_ms: float = 10.0, **inputs) -> STContext:
    for key, value in inputs.items():
        ctx.set(key, value)
    execute(source, ctx, dt_ms)
    return ctx


class TestTimerAttributes:
    def test_q_tracks_done_before_preset(self):
        ctx = STContext()
        _run("t1(IN := TRUE, PT := T#30ms);\nq := t1.Q;\nd := t1.DONE;", ctx)
        assert ctx.get("q") is False
        assert ctx.get("d") is False

    def test_q_tracks_done_across_scans(self):
        ctx = STContext()
        source = "t1(IN := TRUE, PT := T#30ms);\nq := t1.Q;\nd := t1.DONE;"
        seen = []
        for _ in range(4):
            _run(source, ctx)
            seen.append((ctx.get("q"), ctx.get("d")))
        assert seen == [(False, False), (False, False), (True, True), (True, True)]

    def test_q_resets_with_done_when_input_drops(self):
        ctx = STContext()
        source = "t1(IN := enable, PT := T#20ms);\nq := t1.Q;"
        _run(source, ctx, enable=True)
        _run(source, ctx, enable=True)
        assert ctx.get("q") is True
        _run(source, ctx, enable=False)
        assert ctx.get("q") is False

    def test_unknown_timer_attribute_raises(self):
        ctx = STContext()
        with pytest.raises(ValueError, match="unknown timer attribute"):
            _run("t1(IN := TRUE, PT := T#10ms);\nx := t1.BOGUS;", ctx)

    def test_misspelled_done_raises_rather_than_reading_false(self):
        ctx = STContext()
        with pytest.raises(ValueError, match="unknown timer attribute"):
            _run("t1(IN := TRUE, PT := T#10ms);\nx := t1.DOEN;", ctx)

    def test_dotted_read_on_undeclared_timer_raises(self):
        ctx = STContext()
        with pytest.raises(ValueError, match="not a declared timer"):
            _run("x := t_missing.DONE;", ctx)

    def test_accumulated_and_preset_are_readable(self):
        ctx = STContext()
        _run("t1(IN := TRUE, PT := T#30ms);\np := t1.PRESET_MS;\na := t1.ACCUMULATED_MS;", ctx)
        assert ctx.get("p") == 30.0
        assert ctx.get("a") == 10.0
