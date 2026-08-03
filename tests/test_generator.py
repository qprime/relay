from __future__ import annotations
import asyncio
import warnings
from pathlib import Path

import pytest

import relay.plant  # noqa: F401  -- trigger conveyor registration
from relay.generator.errors import (
    SpecValidationError,
    STValidationError,
    UnknownCommStrategy,
    UnknownPlantType,
)
from relay.generator.spec import validate_spec
from relay.generator.st import validate_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import TaskSpec, load_spec


_SPEC_PATH = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"


_PLC_A_ST = """
IF sensor_a_exit AND NOT handoff_signaled THEN
handoff_signaled := TRUE;
_send_plc_b_handoff_signal := TRUE;
END_IF;
"""

_PLC_B_ST = """
IF handoff_signal AND NOT belt_b_enable THEN
belt_b_enable := TRUE;
END_IF;
"""


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


class TestSTValidation:
    def test_rejects_unparseable_block(self):
        spec = _minimal_spec(
            Assertions=["EVENTUALLY(t, within: 100ms)"],
        )
        blocks = {
            "plc_a": "@@@ not valid ST @@@",
            "plc_b": "_send_plc_a_t := TRUE;",
        }
        with pytest.raises(STValidationError) as exc:
            validate_st_blocks(spec, blocks)
        assert "plc_a" in exc.value.per_plc

    def test_rejects_missing_signal_coverage(self):
        # part_at_b is asserted but no route/tag/local produces it
        raw = _minimal_spec().raw
        raw["Plant"]["routes"] = []
        raw["Assertions"] = ["EVENTUALLY(part_at_b, within: 500ms)"]
        spec = TaskSpec(raw=raw)
        blocks = {
            "plc_a": "_send_plc_b_t := TRUE;",
            "plc_b": "noop := FALSE;",
        }
        with pytest.raises(STValidationError) as exc:
            validate_st_blocks(spec, blocks)
        assert "__spec__" in exc.value.per_plc

    def test_passes_for_conveyor_hardcoded_st(self):
        spec = load_spec(_SPEC_PATH)
        blocks = {"plc_a": _PLC_A_ST, "plc_b": _PLC_B_ST}
        validate_st_blocks(spec, blocks)

    def test_warns_on_zero_preset_timer(self):
        spec = _minimal_spec(Assertions=["EVENTUALLY(t, within: 100ms)"])
        blocks = {
            "plc_a": "T1(IN := TRUE, PT := T#0ms);\n_send_plc_b_t := TRUE;",
            "plc_b": "noop := FALSE;",
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_st_blocks(spec, blocks)
        assert any(issubclass(w.category, UserWarning) for w in caught)

    def test_rejects_missing_or_extra_plc_keys(self):
        spec = _minimal_spec()
        blocks = {"plc_a": "_send_plc_b_t := TRUE;"}  # missing plc_b
        with pytest.raises(STValidationError) as exc:
            validate_st_blocks(spec, blocks)
        assert "__spec__" in exc.value.per_plc

    def test_detects_undelivered_tag(self):
        spec = _minimal_spec()
        blocks = {
            "plc_a": "noop := FALSE;",  # declared tag 't' is never emitted
            "plc_b": "noop := FALSE;",
        }
        with pytest.raises(STValidationError) as exc:
            validate_st_blocks(spec, blocks)
        assert "plc_a" in exc.value.per_plc
        assert any("never emitted" in i for i in exc.value.per_plc["plc_a"])


class TestHarnessIntegration:
    def test_simulate_runs_conveyor_end_to_end(self):
        spec = load_spec(_SPEC_PATH)
        blocks = {"plc_a": _PLC_A_ST, "plc_b": _PLC_B_ST}
        trace = asyncio.run(simulate(spec, blocks))
        from relay.verify.assertions import evaluate_all
        results = evaluate_all(spec.assertions, trace)
        assert all(r.passed for r in results), [(r.assertion, r.reason) for r in results]

    def test_simulate_propagates_send_routing_error(self):
        spec = load_spec(_SPEC_PATH)
        bad_st = "_send_plc_unknown_x := TRUE;"
        blocks = {"plc_a": bad_st, "plc_b": _PLC_B_ST}
        with pytest.raises(ValueError, match="_send_"):
            asyncio.run(simulate(spec, blocks, max_scans=1))

    def test_simulate_is_deterministic(self):
        spec = load_spec(_SPEC_PATH)
        blocks = {"plc_a": _PLC_A_ST, "plc_b": _PLC_B_ST}
        trace_a = asyncio.run(simulate(spec, blocks))
        trace_b = asyncio.run(simulate(spec, blocks))
        assert len(trace_a.records) == len(trace_b.records)
        for ra, rb in zip(trace_a.records, trace_b.records):
            assert ra.plc_id == rb.plc_id
            assert ra.clock == rb.clock
            assert dict(ra.outputs.values) == dict(rb.outputs.values)
            assert dict(ra.io.values) == dict(rb.io.values)

    def test_simulate_with_no_st_for_one_plc_raises(self):
        spec = load_spec(_SPEC_PATH)
        blocks = {"plc_a": _PLC_A_ST}  # missing plc_b
        with pytest.raises(ValueError, match="missing ST blocks"):
            asyncio.run(simulate(spec, blocks))
