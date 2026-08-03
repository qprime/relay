from __future__ import annotations
import asyncio
import warnings
from pathlib import Path

import pytest

import relay.plant  # noqa: F401  -- trigger conveyor registration
from relay.generator.errors import (
    SpecValidationError,
    UnknownCommStrategy,
    UnknownPlantType,
)
from relay.generator.spec import validate_spec
from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import TaskSpec, load_spec
from relay.strategies.st_syntax import static_parse_errors


_SPEC_PATH = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"
_GOLDEN_DIR = Path(__file__).parent / "golden"


def _minimal_spec(**overrides) -> TaskSpec:
    raw = {
        "System": {
            "name": "test",
            "plcs": [
                {"id": "plc_a", "role": "x"},
                {"id": "plc_b", "role": "y"},
            ],
        },
        "Comm": {
            "strategy": "tag",
            "tags": [
                {"name": "t", "produced_by": "plc_a", "consumed_by": ["plc_b"]}
            ],
        },
        "Plant": {
            "type": "conveyor",
            "config": {
                "belt_speed_m_per_s": 0.5,
                "sensor_trigger_threshold_m": 0.1,
                "actuator_latency_ms": 50.0,
            },
            "routes": [
                {"sensor": "sensor_a_exit_triggered", "to_plc": "plc_a", "as_key": "sensor_a_exit", "trigger": "edge"},
                {"sensor": "part_at_b", "to_plc": "plc_b", "as_key": "part_at_b", "trigger": "level"},
            ],
            "actuators": [
                {"from_plc": "plc_b", "key": "belt_b_enable", "as": "belt_b_enable_signal"},
            ],
        },
        "Behavior": {
            "plc_a": {
                "triggers": [
                    {
                        "id": "emit_t",
                        "when": {"signal": "sensor_a_exit", "edge": "rising"},
                        "emit": {"tag": "t", "mode": "latched"},
                    }
                ]
            },
            "plc_b": {
                "triggers": [
                    {
                        "id": "belt_on_t",
                        "when": {"signal": "t", "edge": "level"},
                        "emit": {"output": "belt_b_enable", "mode": "latched"},
                    }
                ]
            },
        },
        "Assertions": ["EVENTUALLY(part_at_b, within: 500ms)"],
    }
    for path, value in overrides.items():
        cursor = raw
        parts = path.split(".")
        for p in parts[:-1]:
            cursor = cursor[p]
        cursor[parts[-1]] = value
    return TaskSpec(raw=raw)


def _spec_with_trigger(plc_id: str = "plc_a", **trigger_overrides) -> TaskSpec:
    spec = _minimal_spec()
    trigger = spec.raw["Behavior"][plc_id]["triggers"][0]
    for path, value in trigger_overrides.items():
        cursor = trigger
        parts = path.split(".")
        for p in parts[:-1]:
            cursor = cursor[p]
        if value is _DELETE:
            cursor.pop(parts[-1], None)
        else:
            cursor[parts[-1]] = value
    return spec


_DELETE = object()


def _issues_for(spec: TaskSpec) -> list[str]:
    with pytest.raises(SpecValidationError) as exc:
        validate_spec(spec)
    return exc.value.issues


class TestBehaviorSchema:
    def test_accepts_minimal_rising_latched_trigger(self):
        validate_spec(_minimal_spec())

    def test_rejects_legacy_on_string(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_a"]["on"] = "part_detected -> signal_handoff"
        assert any("no longer supported" in i and "triggers" in i for i in _issues_for(spec))

    def test_rejects_legacy_owns_key(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_a"]["owns"] = ["belt_a"]
        issues = _issues_for(spec)
        assert any("owns has been removed" in i for i in issues)
        assert any("Plant.routes" in i and "Comm.tags" in i for i in issues)

    def test_rejects_missing_triggers_list(self):
        spec = _minimal_spec()
        del spec.raw["Behavior"]["plc_a"]["triggers"]
        assert any("triggers must be a list" in i for i in _issues_for(spec))

    def test_rejects_duplicate_trigger_id_within_plc(self):
        spec = _minimal_spec()
        triggers = spec.raw["Behavior"]["plc_a"]["triggers"]
        triggers.append(
            {
                "id": triggers[0]["id"],
                "when": {"signal": "sensor_a_exit", "edge": "level"},
                "emit": {"output": "other", "mode": "steady"},
            }
        )
        assert any("duplicated within this PLC" in i for i in _issues_for(spec))

    def test_rejects_unknown_signal_in_when(self):
        spec = _spec_with_trigger(**{"when.signal": "no_such_signal"})
        assert any("does not resolve to a Plant route" in i for i in _issues_for(spec))

    def test_rejects_signal_routed_to_a_different_plc(self):
        spec = _spec_with_trigger(**{"when.signal": "part_at_b"})
        assert any("does not resolve" in i for i in _issues_for(spec))

    def test_rejects_emit_tag_not_produced_by_this_plc(self):
        spec = _spec_with_trigger("plc_b", **{"emit": {"tag": "t", "mode": "latched"}})
        assert any("not a Comm tag produced by this PLC" in i for i in _issues_for(spec))

    def test_rejects_both_tag_and_output_in_emit(self):
        spec = _spec_with_trigger(**{"emit.output": "belt_a"})
        assert any("not both" in i for i in _issues_for(spec))

    def test_rejects_neither_tag_nor_output_in_emit(self):
        spec = _spec_with_trigger(**{"emit": {"mode": "latched"}})
        assert any("exactly one of 'tag' or 'output'" in i for i in _issues_for(spec))

    def test_rejects_emit_output_using_scratch_prefix(self):
        spec = _spec_with_trigger(**{"emit": {"output": "_scratch_x", "mode": "latched"}})
        assert any("reserved prefix" in i for i in _issues_for(spec))

    def test_rejects_emit_output_using_send_prefix(self):
        spec = _spec_with_trigger(**{"emit": {"output": "_send_plc_b_x", "mode": "latched"}})
        assert any("reserved prefix" in i for i in _issues_for(spec))

    def test_rejects_emit_output_colliding_with_plant_route_key(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_b"]["triggers"].append(
            {
                "id": "shadow",
                "when": {"signal": "t", "edge": "level"},
                "emit": {"output": "part_at_b", "mode": "latched"},
            }
        )
        assert any("collides with a Plant route as_key" in i for i in _issues_for(spec))

    def test_rejects_emit_output_colliding_with_declared_tag(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_b"]["triggers"].append(
            {
                "id": "shadow",
                "when": {"signal": "t", "edge": "level"},
                "emit": {"output": "t", "mode": "latched"},
            }
        )
        assert any("collides with a declared Comm tag" in i for i in _issues_for(spec))

    def test_rejects_plant_route_collision_from_a_plc_that_never_reads_it(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_a"]["triggers"].append(
            {
                "id": "fabricate",
                "when": {"signal": "sensor_a_exit", "edge": "level"},
                "emit": {"output": "part_at_b", "mode": "steady"},
            }
        )
        assert any("collides with a Plant route as_key" in i for i in _issues_for(spec))

    def test_rejects_pulse_mode_without_duration(self):
        spec = _spec_with_trigger(**{"emit.mode": "pulse"})
        assert any("duration_ms is required for mode 'pulse'" in i for i in _issues_for(spec))

    def test_rejects_pulse_mode_with_zero_duration(self):
        spec = _spec_with_trigger(**{"emit.mode": "pulse", "emit.duration_ms": 0})
        assert any("must be > 0" in i for i in _issues_for(spec))

    def test_rejects_duration_ms_on_non_pulse_mode(self):
        spec = _spec_with_trigger(**{"emit.duration_ms": 50})
        assert any("only valid for mode 'pulse'" in i for i in _issues_for(spec))

    def test_rejects_negative_debounce_ms(self):
        spec = _spec_with_trigger(**{"when.debounce_ms": -5})
        assert any("debounce_ms must be an integer >= 0" in i for i in _issues_for(spec))

    def test_rejects_unknown_edge(self):
        spec = _spec_with_trigger(**{"when.edge": "sideways"})
        assert any("when.edge must be one of" in i for i in _issues_for(spec))

    def test_rejects_unknown_mode(self):
        spec = _spec_with_trigger(**{"emit.mode": "toggle"})
        assert any("emit.mode must be one of" in i for i in _issues_for(spec))

    def test_rejects_two_triggers_emitting_same_target(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_b"]["triggers"].append(
            {
                "id": "belt_again",
                "when": {"signal": "part_at_b", "edge": "rising"},
                "emit": {"output": "belt_b_enable", "mode": "steady"},
            }
        )
        assert any("one trigger per target" in i for i in _issues_for(spec))

    def test_accepts_two_triggers_emitting_distinct_targets(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_b"]["triggers"].append(
            {
                "id": "alarm_on_part",
                "when": {"signal": "part_at_b", "edge": "rising"},
                "emit": {"output": "alarm", "mode": "steady"},
            }
        )
        validate_spec(spec)

    def test_rejects_trigger_id_not_snake_case(self):
        spec = _spec_with_trigger(**{"id": "Handoff On Exit"})
        assert any("must match" in i for i in _issues_for(spec))

    def test_collects_multiple_trigger_issues(self):
        spec = _spec_with_trigger(
            **{"id": "BAD ID", "when.signal": "nope", "when.edge": "sideways"}
        )
        assert len(_issues_for(spec)) >= 3

    def test_uncovered_assertion_signal_is_rejected_at_spec_time(self):
        spec = _minimal_spec(Assertions=["EVENTUALLY(ghost_signal, within: 100ms)"])
        assert any("ghost_signal" in i and "not covered" in i for i in _issues_for(spec))

    def test_assertion_signal_covered_by_trigger_emit_target(self):
        spec = _minimal_spec(Assertions=["EVENTUALLY(belt_b_enable, within: 100ms)"])
        validate_spec(spec)


class TestSpecValidation:
    def test_rejects_missing_system_plcs(self):
        raw = _minimal_spec().raw
        del raw["System"]["plcs"]
        with pytest.raises(SpecValidationError) as exc:
            validate_spec(TaskSpec(raw=raw))
        assert any("plcs" in i for i in exc.value.issues)

    def test_rejects_unsupported_assertion_form(self):
        spec = _minimal_spec(Assertions=["ALWAYS(x)"])
        with pytest.raises(SpecValidationError) as exc:
            validate_spec(spec)
        assert any("ALWAYS" in i or "not a recognized form" in i for i in exc.value.issues)

    def test_rejects_missing_comm_strategy(self):
        raw = _minimal_spec().raw
        del raw["Comm"]["strategy"]
        with pytest.raises(SpecValidationError) as exc:
            validate_spec(TaskSpec(raw=raw))
        assert any("Comm.strategy" in i for i in exc.value.issues)

    def test_rejects_unknown_comm_strategy(self):
        raw = _minimal_spec().raw
        raw["Comm"]["strategy"] = "nonsense"
        with pytest.raises(UnknownCommStrategy):
            validate_spec(TaskSpec(raw=raw))

    def test_rejects_missing_plant_type(self):
        raw = _minimal_spec().raw
        del raw["Plant"]["type"]
        with pytest.raises(SpecValidationError) as exc:
            validate_spec(TaskSpec(raw=raw))
        assert any("Plant.type" in i for i in exc.value.issues)

    def test_rejects_unknown_plant_type(self):
        raw = _minimal_spec().raw
        raw["Plant"]["type"] = "imaginary"
        with pytest.raises(UnknownPlantType):
            validate_spec(TaskSpec(raw=raw))

    def test_rejects_plant_route_to_unknown_plc(self):
        raw = _minimal_spec().raw
        raw["Plant"]["routes"] = [
            {"sensor": "x", "to_plc": "plc_z", "as_key": "x", "trigger": "level"}
        ]
        with pytest.raises(SpecValidationError) as exc:
            validate_spec(TaskSpec(raw=raw))
        assert any("plc_z" in i for i in exc.value.issues)

    def test_collects_all_issues_not_just_first(self):
        raw = _minimal_spec().raw
        raw["System"]["name"] = "Bad Name With Spaces"
        raw["Assertions"] = ["BOGUS(x)"]
        raw["Plant"]["routes"] = [
            {"sensor": "x", "to_plc": "plc_z", "as_key": "x", "trigger": "edge"}
        ]
        with pytest.raises(SpecValidationError) as exc:
            validate_spec(TaskSpec(raw=raw))
        # at least three distinct issues
        assert len(exc.value.issues) >= 3, exc.value.issues

    def test_passes_for_migrated_conveyor_yaml(self):
        spec = load_spec(_SPEC_PATH)
        validate_spec(spec)


class TestCompiler:
    def test_compiles_rising_latched_to_known_st(self):
        spec = _spec_with_trigger(**{"when.edge": "rising", "emit.mode": "latched"})
        assert compile_st_blocks(spec)["plc_a"] == (
            "_scratch_edge_emit_t := sensor_a_exit AND NOT _scratch_prev_emit_t;\n"
            "_scratch_prev_emit_t := sensor_a_exit;\n"
            "IF _scratch_edge_emit_t THEN\n"
            "_scratch_latched_emit_t := TRUE;\n"
            "END_IF;\n"
            "_send_plc_b_t := _scratch_latched_emit_t;"
        )

    def test_compiles_falling_latched_to_known_st(self):
        spec = _spec_with_trigger(**{"when.edge": "falling", "emit.mode": "latched"})
        assert compile_st_blocks(spec)["plc_a"] == (
            "_scratch_edge_emit_t := NOT sensor_a_exit AND _scratch_prev_emit_t;\n"
            "_scratch_prev_emit_t := sensor_a_exit;\n"
            "IF _scratch_edge_emit_t THEN\n"
            "_scratch_latched_emit_t := TRUE;\n"
            "END_IF;\n"
            "_send_plc_b_t := _scratch_latched_emit_t;"
        )

    def test_compiles_level_latched_to_known_st(self):
        spec = _spec_with_trigger(**{"when.edge": "level", "emit.mode": "latched"})
        assert compile_st_blocks(spec)["plc_a"] == (
            "IF sensor_a_exit THEN\n"
            "_scratch_latched_emit_t := TRUE;\n"
            "END_IF;\n"
            "_send_plc_b_t := _scratch_latched_emit_t;"
        )

    def test_compiles_steady_to_known_st(self):
        spec = _spec_with_trigger(**{"when.edge": "level", "emit.mode": "steady"})
        assert compile_st_blocks(spec)["plc_a"] == "_send_plc_b_t := sensor_a_exit;"

    def test_compiles_rising_pulse_with_ton(self):
        spec = _spec_with_trigger(
            **{"when.edge": "rising", "emit.mode": "pulse", "emit.duration_ms": 30}
        )
        assert compile_st_blocks(spec)["plc_a"] == (
            "_scratch_edge_emit_t := sensor_a_exit AND NOT _scratch_prev_emit_t;\n"
            "_scratch_prev_emit_t := sensor_a_exit;\n"
            "IF _scratch_edge_emit_t THEN\n"
            "_scratch_pulse_emit_t := TRUE;\n"
            "END_IF;\n"
            "_scratch_ton_emit_t(IN := _scratch_pulse_emit_t, PT := T#30ms);\n"
            "IF _scratch_ton_emit_t.Q THEN\n"
            "_scratch_pulse_emit_t := FALSE;\n"
            "END_IF;\n"
            "_send_plc_b_t := _scratch_pulse_emit_t;"
        )

    def test_compiles_with_debounce_emits_stability_ton(self):
        spec = _spec_with_trigger(
            **{"when.edge": "level", "emit.mode": "steady", "when.debounce_ms": 30}
        )
        assert compile_st_blocks(spec)["plc_a"] == (
            "_scratch_debounce_emit_t(IN := sensor_a_exit, PT := T#30ms);\n"
            "_scratch_stable_emit_t := _scratch_debounce_emit_t.Q;\n"
            "_send_plc_b_t := _scratch_stable_emit_t;"
        )

    def test_debounce_and_pulse_compose_in_one_trigger(self):
        spec = _spec_with_trigger(
            **{
                "when.edge": "rising",
                "when.debounce_ms": 20,
                "emit.mode": "pulse",
                "emit.duration_ms": 30,
            }
        )
        source = compile_st_blocks(spec)["plc_a"]
        assert source.startswith("_scratch_debounce_emit_t(IN := sensor_a_exit, PT := T#20ms);")
        assert "_scratch_edge_emit_t := _scratch_stable_emit_t AND NOT _scratch_prev_emit_t;" in source
        assert "_scratch_ton_emit_t(IN := _scratch_pulse_emit_t, PT := T#30ms);" in source
        assert not static_parse_errors(source)

    def test_emits_one_send_per_declared_consumer(self):
        spec = _minimal_spec()
        spec.raw["Comm"]["tags"][0]["consumed_by"] = ["plc_b", "plc_c"]
        spec.raw["System"]["plcs"].append({"id": "plc_c", "role": "z"})
        source = compile_st_blocks(spec)["plc_a"]
        assert "_send_plc_b_t := _scratch_latched_emit_t;" in source
        assert "_send_plc_c_t := _scratch_latched_emit_t;" in source

    def test_zero_trigger_plc_compiles_to_empty_body(self):
        spec = _minimal_spec()
        spec.raw["Behavior"]["plc_a"]["triggers"] = []
        assert compile_st_blocks(spec)["plc_a"] == ""
        assert static_parse_errors("") == []

    def test_plc_absent_from_behavior_compiles_to_empty_body(self):
        spec = _minimal_spec()
        del spec.raw["Behavior"]["plc_a"]
        assert compile_st_blocks(spec)["plc_a"] == ""

    def test_compiler_covers_every_declared_plc(self):
        spec = _minimal_spec()
        assert set(compile_st_blocks(spec)) == set(spec.plc_ids)

    def test_compiler_is_pure(self):
        spec = load_spec(_SPEC_PATH)
        assert compile_st_blocks(spec) == compile_st_blocks(spec)

    def test_compiler_output_passes_static_st_parser(self):
        spec = load_spec(_SPEC_PATH)
        for plc_id, source in compile_st_blocks(spec).items():
            assert static_parse_errors(source) == [], (plc_id, source)

    @pytest.mark.parametrize("edge", ["rising", "falling", "level"])
    @pytest.mark.parametrize("mode", ["latched", "steady", "pulse"])
    @pytest.mark.parametrize("debounce_ms", [0, 30])
    @pytest.mark.parametrize("kind", ["tag", "output"])
    def test_compiler_output_passes_static_st_parser_for_every_template(
        self, edge, mode, debounce_ms, kind
    ):
        emit = {"mode": mode}
        emit["tag" if kind == "tag" else "output"] = "t" if kind == "tag" else "belt_a"
        if mode == "pulse":
            emit["duration_ms"] = 30
        spec = _spec_with_trigger(
            **{"when.edge": edge, "when.debounce_ms": debounce_ms, "emit": emit}
        )
        validate_spec(spec)
        source = compile_st_blocks(spec)["plc_a"]
        assert static_parse_errors(source) == [], source


class TestGoldenST:
    def test_plc_a_golden(self):
        spec = load_spec(_SPEC_PATH)
        expected = (_GOLDEN_DIR / "plc_a.st").read_text()
        assert compile_st_blocks(spec)["plc_a"] + "\n" == expected

    def test_plc_b_golden(self):
        spec = load_spec(_SPEC_PATH)
        expected = (_GOLDEN_DIR / "plc_b.st").read_text()
        assert compile_st_blocks(spec)["plc_b"] + "\n" == expected


def _evaluate_conveyor(spec: TaskSpec, blocks: dict[str, str]):
    from relay.verify.assertions import evaluate_all

    trace = asyncio.run(simulate(spec, blocks))
    return evaluate_all(spec.assertions, trace)


class TestConveyorMigration:
    def test_migrated_spec_validates(self):
        validate_spec(load_spec(_SPEC_PATH))

    def test_migrated_spec_compiles(self):
        spec = load_spec(_SPEC_PATH)
        blocks = compile_st_blocks(spec)
        assert set(blocks) == set(spec.plc_ids)
        for source in blocks.values():
            assert static_parse_errors(source) == []

    def test_migrated_spec_eventually_part_at_b(self):
        spec = load_spec(_SPEC_PATH)
        results = _evaluate_conveyor(spec, compile_st_blocks(spec))
        result = next(r for r in results if "EVENTUALLY" in r.assertion)
        assert result.passed, result.reason

    def test_migrated_spec_precedes_handoff_then_belt(self):
        spec = load_spec(_SPEC_PATH)
        results = _evaluate_conveyor(spec, compile_st_blocks(spec))
        result = next(r for r in results if "PRECEDES" in r.assertion)
        assert result.passed, result.reason

    def test_migrated_spec_negative_no_emit_fails_assertions(self):
        spec = load_spec(_SPEC_PATH)
        blocks = compile_st_blocks(spec)
        blocks["plc_a"] = ""
        results = _evaluate_conveyor(spec, blocks)
        assert not all(r.passed for r in results)

    def test_migrated_spec_deterministic(self):
        spec = load_spec(_SPEC_PATH)
        blocks = compile_st_blocks(spec)
        trace_a = asyncio.run(simulate(spec, blocks))
        trace_b = asyncio.run(simulate(spec, blocks))
        assert len(trace_a.records) == len(trace_b.records)
        for ra, rb in zip(trace_a.records, trace_b.records):
            assert ra.plc_id == rb.plc_id
            assert ra.clock == rb.clock
            assert dict(ra.outputs.values) == dict(rb.outputs.values)
            assert dict(ra.io.values) == dict(rb.io.values)

    def test_compiled_st_leaves_no_scratch_names_in_trace(self):
        spec = load_spec(_SPEC_PATH)
        trace = asyncio.run(simulate(spec, compile_st_blocks(spec)))
        for record in trace.records:
            assert not any(k.startswith("_scratch_") for k in record.outputs.values)
            assert not any(k.startswith("_scratch_") for k in record.io.values)


class TestHarnessIntegration:
    def test_simulate_propagates_send_routing_error(self):
        spec = load_spec(_SPEC_PATH)
        blocks = compile_st_blocks(spec)
        blocks["plc_a"] = "_send_plc_unknown_x := TRUE;"
        with pytest.raises(ValueError, match="_send_"):
            asyncio.run(simulate(spec, blocks, max_scans=1))

    def test_simulate_with_no_st_for_one_plc_raises(self):
        spec = load_spec(_SPEC_PATH)
        blocks = compile_st_blocks(spec)
        del blocks["plc_b"]
        with pytest.raises(ValueError, match="missing ST blocks"):
            asyncio.run(simulate(spec, blocks))
