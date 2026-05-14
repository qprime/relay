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
                {"sensor": "part_at_b", "to_plc": "plc_b", "as_key": "part_at_b", "trigger": "level"},
            ],
            "actuators": [
                {"from_plc": "plc_b", "key": "belt_b_enable", "as": "belt_b_enable_signal"},
            ],
        },
        "Behavior": {
            "plc_a": {"owns": ["x"], "on": "rule"},
            "plc_b": {"owns": ["y"], "on": "rule"},
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
