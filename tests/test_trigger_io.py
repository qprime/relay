from __future__ import annotations
import io as io_module
import json
from pathlib import Path

import pytest

import relay.plant  # noqa: F401  -- trigger conveyor registration
from relay.generator.behavior import Trigger, TriggerEmit, TriggerWhen, compile_plc, parse_triggers
from relay.generator.spec import validate_spec
from relay.generator.st import compile_st_blocks
from relay.generator.trigger_io import (
    dump_jsonl,
    load_jsonl,
    trigger_from_dict,
    trigger_to_dict,
)
from relay.spec.schema import load_spec


_CONVEYOR_SPEC = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"
_COVERAGE_SPEC = Path(__file__).parent / "fixtures" / "trigger_ir_coverage.yaml"
_GOLDEN_DIR = Path(__file__).parent / "golden"

_MINIMAL_LINE = {
    "plc_id": "plc_a",
    "id": "t",
    "signal": "sensor_a_exit",
    "edge": "rising",
    "debounce_ms": 0,
    "target": "belt_a_run",
    "target_kind": "output",
    "mode": "latched",
    "duration_ms": None,
}


def _spec_triggers(path: Path):
    spec = load_spec(path)
    return spec, {p: parse_triggers(spec.behavior.get(p) or {}) for p in spec.plc_ids}


def _dump_to_text(triggers) -> str:
    stream = io_module.StringIO()
    dump_jsonl(triggers, stream)
    return stream.getvalue()


def _load_from_text(text: str):
    return load_jsonl(io_module.StringIO(text))


def _round_trip(triggers):
    return _load_from_text(_dump_to_text(triggers))


def _trigger(**overrides) -> Trigger:
    when = {"signal": "sensor_a_exit", "edge": "rising", "debounce_ms": 0}
    emit = {
        "target": "belt_a_run",
        "target_kind": "output",
        "mode": "latched",
        "duration_ms": None,
    }
    when.update({k: v for k, v in overrides.items() if k in when})
    emit.update({k: v for k, v in overrides.items() if k in emit})
    return Trigger(
        id=overrides.get("id", "t"),
        when=TriggerWhen(**when),
        emit=TriggerEmit(**emit),
    )


class TestTriggerIORoundTrip:
    def test_conveyor_triggers_survive_round_trip(self):
        _, triggers = _spec_triggers(_CONVEYOR_SPEC)
        assert _round_trip(triggers) == triggers

    def test_coverage_fixture_survives_round_trip(self):
        _, triggers = _spec_triggers(_COVERAGE_SPEC)
        populated = {p: t for p, t in triggers.items() if t}
        assert _round_trip(triggers) == populated

    def test_empty_trigger_set_round_trips(self):
        assert _dump_to_text({}) == ""
        assert _load_from_text("") == {}

    def test_plc_with_no_triggers_is_absent_after_round_trip(self):
        """Documented lossiness, not fidelity: a PLC contributes zero lines when
        it has no triggers, so it cannot reappear as a key. A header-line format
        would flip this — that must be a conscious change, not an accident."""
        triggers = {"plc_a": [_trigger()], "plc_b": []}
        assert _round_trip(triggers) == {"plc_a": [_trigger()]}

    def test_multi_plc_grouping_preserved(self):
        triggers = {
            "plc_b": [_trigger(id="b_one"), _trigger(id="b_two", target="belt_b_run")],
            "plc_a": [_trigger(id="a_one")],
        }
        loaded = _round_trip(triggers)
        assert list(loaded) == ["plc_b", "plc_a"]
        assert [t.id for t in loaded["plc_b"]] == ["b_one", "b_two"]

    def test_trigger_dict_round_trips_without_a_stream(self):
        trigger = _trigger(edge="falling", mode="pulse", duration_ms=30, debounce_ms=20)
        assert trigger_from_dict(trigger_to_dict("plc_a", trigger)) == ("plc_a", trigger)


class TestTriggerIOFormat:
    def test_line_count_equals_trigger_count(self):
        _, triggers = _spec_triggers(_COVERAGE_SPEC)
        lines = _dump_to_text(triggers).splitlines()
        assert len(lines) == sum(len(t) for t in triggers.values())

    def test_each_line_has_nine_required_keys(self):
        _, triggers = _spec_triggers(_COVERAGE_SPEC)
        for line in _dump_to_text(triggers).splitlines():
            assert set(json.loads(line)) == {
                "plc_id",
                "id",
                "signal",
                "edge",
                "debounce_ms",
                "target",
                "target_kind",
                "mode",
                "duration_ms",
            }

    def test_key_order_does_not_affect_bytes(self):
        forward = Trigger(
            id="t",
            when=TriggerWhen(signal="sensor_a_exit", edge="rising", debounce_ms=0),
            emit=TriggerEmit(
                target="belt_a_run", target_kind="output", mode="latched", duration_ms=None
            ),
        )
        reverse = Trigger(
            id="t",
            emit=TriggerEmit(
                duration_ms=None, mode="latched", target_kind="output", target="belt_a_run"
            ),
            when=TriggerWhen(debounce_ms=0, edge="rising", signal="sensor_a_exit"),
        )
        assert _dump_to_text({"plc_a": [forward]}) == _dump_to_text({"plc_a": [reverse]})

    def test_duration_ms_is_null_for_non_pulse_modes(self):
        _, triggers = _spec_triggers(_COVERAGE_SPEC)
        lines = [json.loads(line) for line in _dump_to_text(triggers).splitlines()]
        by_mode = {line["mode"] for line in lines}
        assert "pulse" in by_mode and by_mode - {"pulse"}
        for line in lines:
            if line["mode"] == "pulse":
                assert isinstance(line["duration_ms"], int)
            else:
                assert line["duration_ms"] is None

    def test_blank_lines_are_skipped_on_load(self):
        _, triggers = _spec_triggers(_CONVEYOR_SPEC)
        text = _dump_to_text(triggers)
        padded = "\n" + text.replace("\n", "\n\n") + "\n"
        assert _load_from_text(padded) == triggers


class TestTriggerIOGuard:
    @pytest.mark.parametrize("field", ["id", "signal", "target"])
    def test_none_string_field_raises_at_dump_naming_field(self, field):
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": [_trigger(**{field: None})]})
        assert field in str(exc.value)
        assert "NoneType" in str(exc.value)

    def test_bool_debounce_ms_raises_at_dump(self):
        """Unreachable through parse_triggers — int(True) is 1 — so the Trigger
        is constructed directly. Defense in depth for hand-built IR."""
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": [_trigger(debounce_ms=True)]})
        assert "debounce_ms" in str(exc.value)
        assert "bool" in str(exc.value)

    def test_unknown_edge_raises_at_dump(self):
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": [_trigger(edge="sideways")]})
        assert "edge" in str(exc.value)

    def test_unknown_mode_raises_at_dump(self):
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": [_trigger(mode="toggled")]})
        assert "mode" in str(exc.value)

    def test_non_int_duration_ms_raises_at_dump_on_pulse(self):
        """'30ms' serializes as a JSON string and compiles to PT := T#30msms."""
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": [_trigger(mode="pulse", duration_ms="30ms")]})
        assert "duration_ms" in str(exc.value)

    def test_none_duration_ms_raises_at_dump_on_pulse(self):
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": [_trigger(mode="pulse", duration_ms=None)]})
        assert "duration_ms" in str(exc.value)

    @pytest.mark.parametrize("mode", ["latched", "steady"])
    def test_duration_ms_on_non_pulse_mode_raises_at_dump(self, mode):
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": [_trigger(mode=mode, duration_ms=30)]})
        assert "duration_ms" in str(exc.value)

    def test_unvalidated_spec_dump_fails_loudly(self):
        """parse_triggers does no validation, so a behavior entry missing 'id'
        yields Trigger(id=None) and ST containing the literal string None."""
        triggers = parse_triggers(
            {
                "triggers": [
                    {
                        "when": {"signal": "sensor_a_exit", "edge": "rising"},
                        "emit": {"output": "belt_a_run", "mode": "latched"},
                    }
                ]
            }
        )
        with pytest.raises(TypeError) as exc:
            _dump_to_text({"plc_a": triggers})
        assert "id" in str(exc.value)


class TestTriggerIOErrors:
    def test_malformed_line_raises_naming_file_line_number(self):
        good = json.dumps(_MINIMAL_LINE)
        with pytest.raises(ValueError) as exc:
            _load_from_text(f"{good}\n{good}\n{{not json\n{good}\n")
        assert "line 3" in str(exc.value)

    @pytest.mark.parametrize(
        "bad,kind",
        [("[1, 2, 3]", "list"), ("42", "int"), ('"hello"', "str"), ("null", "NoneType")],
    )
    def test_non_object_line_raises_naming_file_line_number(self, bad, kind):
        good = json.dumps(_MINIMAL_LINE)
        with pytest.raises(ValueError) as exc:
            _load_from_text(f"{good}\n{good}\n{bad}\n{good}\n")
        assert "line 3" in str(exc.value)
        assert f"JSON {kind}, not an object" in str(exc.value)

    @pytest.mark.parametrize("missing", sorted(_MINIMAL_LINE))
    def test_missing_required_key_raises_naming_key_and_line(self, missing):
        text = json.dumps({k: v for k, v in _MINIMAL_LINE.items() if k != missing})
        with pytest.raises(KeyError) as exc:
            _load_from_text(text + "\n")
        assert missing in str(exc.value)
        assert "line 1" in str(exc.value)

    def test_unreadable_debounce_ms_raises_naming_file_line_number(self):
        good = json.dumps(_MINIMAL_LINE)
        bad = json.dumps({**_MINIMAL_LINE, "debounce_ms": "soon"})
        with pytest.raises(ValueError) as exc:
            _load_from_text(f"{good}\n{good}\n{bad}\n{good}\n")
        assert "line 3" in str(exc.value)


class TestGoldenTriggers:
    def test_conveyor_triggers_match_golden(self):
        _, triggers = _spec_triggers(_CONVEYOR_SPEC)
        assert _dump_to_text(triggers) == (_GOLDEN_DIR / "conveyor_triggers.jsonl").read_text()

    def test_coverage_triggers_match_golden(self):
        _, triggers = _spec_triggers(_COVERAGE_SPEC)
        assert _dump_to_text(triggers) == (_GOLDEN_DIR / "coverage_triggers.jsonl").read_text()

    def test_golden_triggers_compile_to_same_st(self):
        """Scoped to the loaded keys: a zero-trigger PLC is absent from the
        loaded dict by design but present in compile_st_blocks."""
        spec = load_spec(_CONVEYOR_SPEC)
        with (_GOLDEN_DIR / "conveyor_triggers.jsonl").open() as stream:
            loaded = load_jsonl(stream)
        blocks = compile_st_blocks(spec)
        assert loaded
        assert {p: compile_plc(t, spec) for p, t in loaded.items()} == {
            p: blocks[p] for p in loaded
        }

    def test_target_kind_is_present_in_every_line(self):
        _, triggers = _spec_triggers(_COVERAGE_SPEC)
        for line in _dump_to_text(triggers).splitlines():
            assert json.loads(line)["target_kind"] in ("tag", "output")

    def test_line_missing_target_kind_fails_to_load(self):
        """target_kind is on the wire, not re-derived from spec context. No ST
        diff can pin this: validate_spec forbids output/tag name collisions, so
        a tag-name heuristic agrees with target_kind on every legal spec."""
        text = json.dumps({k: v for k, v in _MINIMAL_LINE.items() if k != "target_kind"})
        with pytest.raises(KeyError) as exc:
            _load_from_text(text + "\n")
        assert "target_kind" in str(exc.value)


class TestValidatedSpecAlwaysSerializes:
    @pytest.mark.parametrize("path", [_CONVEYOR_SPEC, _COVERAGE_SPEC], ids=["conveyor", "coverage"])
    def test_validate_spec_pass_implies_dump_succeeds(self, path):
        spec, triggers = _spec_triggers(path)
        validate_spec(spec)
        assert _dump_to_text(triggers)
