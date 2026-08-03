from __future__ import annotations

import pytest

import relay.plant  # noqa: F401  -- trigger conveyor registration
from relay.clock import DEFAULT_SCAN_PERIOD_MS
from relay.generator.behavior import Trigger, TriggerEmit, TriggerWhen, compile_plc
from relay.spec.schema import TaskSpec
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


_SIGNAL = "src"
_OUTPUT = "out"


def _drive(edge, mode, sequence, *, debounce_ms=0, duration_ms=None) -> list[bool]:
    """Run compiled trigger ST over an input sequence; return the emitted sequence."""
    trigger = Trigger(
        id="t",
        when=TriggerWhen(signal=_SIGNAL, edge=edge, debounce_ms=debounce_ms),
        emit=TriggerEmit(
            target=_OUTPUT, target_kind="output", mode=mode, duration_ms=duration_ms
        ),
    )
    source = compile_plc([trigger], TaskSpec(raw={}))
    ctx = STContext()
    emitted: list[bool] = []
    for value in sequence:
        ctx.set(_SIGNAL, bool(value))
        execute(source, ctx, DEFAULT_SCAN_PERIOD_MS)
        emitted.append(bool(ctx.get(_OUTPUT)))
    return emitted


def _bits(values: list[bool]) -> str:
    return "".join("1" if v else "0" for v in values)


class TestTriggerSemantics:
    def test_rising_fires_once_on_transition(self):
        assert _bits(_drive("rising", "steady", [0, 1, 1, 1, 0, 1])) == "010001"

    def test_falling_fires_on_release(self):
        assert _bits(_drive("falling", "steady", [0, 1, 1, 0, 0, 1, 0])) == "0001001"

    def test_level_fires_while_true(self):
        assert _bits(_drive("level", "steady", [0, 1, 1, 0, 1])) == "01101"

    def test_latched_stays_high_after_source_clears(self):
        assert _bits(_drive("rising", "latched", [0, 1, 1, 0, 0, 0])) == "011111"

    def test_falling_latched_matches_specified_behavior(self):
        assert _bits(_drive("falling", "latched", [0, 0, 1, 1, 1, 0, 0, 0])) == "00000111"

    def test_steady_follows_source_down(self):
        assert _bits(_drive("level", "steady", [0, 0, 1, 1, 1, 0, 0, 0])) == "00111000"

    def test_pulse_deasserts_after_duration(self):
        emitted = _drive(
            "rising", "pulse", [0, 1, 1, 1, 1, 1, 1, 1], duration_ms=30
        )
        assert _bits(emitted) == "01100000"

    def test_pulse_width_scales_with_duration(self):
        emitted = _drive(
            "rising", "pulse", [0, 1, 1, 1, 1, 1, 1, 1], duration_ms=50
        )
        assert _bits(emitted) == "01111000"

    def test_debounce_rejects_glitch_shorter_than_window(self):
        emitted = _drive("level", "steady", [0, 1, 1, 0, 0, 0], debounce_ms=30)
        assert _bits(emitted) == "000000"

    def test_debounce_passes_sustained_signal(self):
        emitted = _drive(
            "rising", "latched", [0, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0], debounce_ms=30
        )
        assert _bits(emitted) == "00000001111"
