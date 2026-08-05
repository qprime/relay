from __future__ import annotations
import ast
import asyncio
import io as io_module
import json
from pathlib import Path

import pytest

import relay.plant  # noqa: F401  -- trigger conveyor registration
from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import load_spec
from relay.verdict_io import dump_json, load_json, verdict_to_dict
from relay.verify.assertions import AssertionResult, evaluate_all


_SPEC_PATH = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"
_GOLDEN_VERDICT = Path(__file__).parent / "golden" / "conveyor_verdict.json"
_VERDICT_IO = Path(__file__).parent.parent / "relay" / "verdict_io.py"


def _conveyor_results():
    spec = load_spec(_SPEC_PATH)
    trace = asyncio.run(simulate(spec, compile_st_blocks(spec)))
    return evaluate_all(spec.assertions, trace)


def _dump_to_text(results) -> str:
    stream = io_module.StringIO()
    dump_json(results, stream)
    return stream.getvalue()


def _load_from_text(text: str):
    return load_json(io_module.StringIO(text))


def _round_trip(results):
    return _load_from_text(_dump_to_text(results))


def _result(**overrides) -> AssertionResult:
    fields = {
        "assertion": "EVENTUALLY(part_at_b, within: 500ms)",
        "passed": True,
        "reason": "signal 'part_at_b' true at 60.0ms",
        "observed_gap_ms": None,
    }
    fields.update(overrides)
    return AssertionResult(**fields)


class TestVerdictIORoundTrip:
    def test_conveyor_verdict_survives_round_trip(self):
        results = _conveyor_results()
        loaded = _round_trip(results)
        assert len(loaded) == len(results)
        for original, restored in zip(results, loaded):
            assert restored["assertion"] == original.assertion
            assert restored["passed"] == original.passed
            assert restored["reason"] == original.reason
            assert restored["observed_gap_ms"] == original.observed_gap_ms

    def test_empty_results_round_trips(self):
        assert _round_trip([]) == []

    def test_none_gap_round_trips_as_none(self):
        assert _round_trip([_result(observed_gap_ms=None)])[0]["observed_gap_ms"] is None

    def test_zero_gap_round_trips_as_zero_not_none(self):
        restored = _round_trip([_result(observed_gap_ms=0.0)])[0]
        assert restored["observed_gap_ms"] == 0.0
        assert restored["observed_gap_ms"] is not None, (
            "0.0 is falsey; a serializer written with `if gap:` collapses it to "
            "null, and the conveyor PRECEDES gap is 0.0ms today"
        )

    def test_negative_gap_survives_with_sign(self):
        restored = _round_trip([_result(passed=False, observed_gap_ms=-40.0)])[0]
        assert restored["observed_gap_ms"] == -40.0

    def test_unparseable_assertion_result_round_trips(self):
        result = _result(
            assertion="NONSENSE(x)",
            passed=False,
            reason="unrecognized assertion form: NONSENSE(x)",
            observed_gap_ms=None,
        )
        restored = _round_trip([result])[0]
        assert restored["passed"] is False
        assert restored["observed_gap_ms"] is None


class TestVerdictFormat:
    def test_top_level_keys(self):
        document = json.loads(_dump_to_text(_conveyor_results()))
        assert set(document) == {"results", "passed", "counts"}

    def test_passed_is_conjunction_of_results(self):
        document = json.loads(_dump_to_text([_result(), _result(passed=False)]))
        assert document["passed"] is False

    def test_passed_is_true_for_empty_results(self):
        document = json.loads(_dump_to_text([]))
        assert document["passed"] is True
        assert document["counts"] == {"total": 0, "passed": 0, "failed": 0}

    def test_counts_match_results(self):
        document = json.loads(
            _dump_to_text([_result(), _result(passed=False), _result()])
        )
        assert document["counts"] == {"total": 3, "passed": 2, "failed": 1}

    def test_every_result_has_four_keys(self):
        document = json.loads(_dump_to_text(_conveyor_results()))
        for entry in document["results"]:
            assert set(entry) == {
                "assertion",
                "passed",
                "reason",
                "observed_gap_ms",
            }

    def test_key_order_does_not_affect_bytes(self):
        forward = verdict_to_dict(_result(observed_gap_ms=1.5))
        reverse = {k: forward[k] for k in reversed(list(forward))}
        assert json.dumps(forward, sort_keys=True) == json.dumps(reverse, sort_keys=True)

    @pytest.mark.parametrize(
        "gap", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
    )
    def test_non_finite_gap_raises_at_dump(self, gap):
        with pytest.raises(ValueError) as exc:
            _dump_to_text([_result(observed_gap_ms=gap)])
        assert "observed_gap_ms" in str(exc.value)


def _entry(**overrides) -> dict:
    fields = {
        "assertion": "EVENTUALLY(part_at_b, within: 500ms)",
        "passed": True,
        "reason": "signal 'part_at_b' true at 60.0ms",
        "observed_gap_ms": None,
    }
    fields.update(overrides)
    return fields


def _load_entry(entry: dict):
    return _load_from_text(json.dumps({"results": [entry], "passed": True}))


class TestVerdictLoadGuards:
    """Same predicate on both sides: a wire format exists to be read by
    something that did not just write it, so the load path is the one facing
    untrusted bytes."""

    @pytest.mark.parametrize("value", [123, None, ["x"]])
    def test_non_string_assertion_rejected_at_load(self, value):
        with pytest.raises(ValueError) as exc:
            _load_entry(_entry(assertion=value))
        assert "assertion" in str(exc.value)

    @pytest.mark.parametrize("value", [None, 7, {"a": 1}])
    def test_non_string_reason_rejected_at_load(self, value):
        with pytest.raises(ValueError) as exc:
            _load_entry(_entry(reason=value))
        assert "reason" in str(exc.value)

    @pytest.mark.parametrize("value", ["yes", "false", [], 1, 0, None])
    def test_non_bool_passed_is_rejected_not_coerced(self, value):
        """bool(data['passed']) loads a corrupt verdict with an inverted
        pass/fail rather than failing: 'false' becomes True, [] becomes
        False. A wrong answer that looks right is worse than no answer."""
        with pytest.raises(ValueError) as exc:
            _load_entry(_entry(passed=value))
        assert "passed" in str(exc.value)

    @pytest.mark.parametrize("value", ["not-a-number", True, [1]])
    def test_non_numeric_gap_rejected_at_load(self, value):
        with pytest.raises(ValueError) as exc:
            _load_entry(_entry(observed_gap_ms=value))
        assert "observed_gap_ms" in str(exc.value)

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_gap_rejected_at_load(self, literal):
        """Rejected as unrepresentable at dump; json.loads accepts these
        literals, so the load side must reject them by the same predicate."""
        fields = _entry()
        del fields["observed_gap_ms"]
        entry = json.dumps(fields)[:-1] + f', "observed_gap_ms": {literal}}}'
        with pytest.raises(ValueError) as exc:
            _load_from_text('{"passed": true, "results": [' + entry + "]}")
        assert "observed_gap_ms" in str(exc.value)

    def test_guard_failure_names_the_result_index(self):
        text = json.dumps(
            {"results": [_entry(), _entry(passed="yes")], "passed": True}
        )
        with pytest.raises(ValueError) as exc:
            _load_from_text(text)
        assert "results[1]" in str(exc.value)

    def test_valid_entry_still_loads(self):
        loaded = _load_entry(_entry(passed=False, observed_gap_ms=-40.0))
        assert loaded == [_entry(passed=False, observed_gap_ms=-40.0)]


class TestVerdictDumpGuards:
    @pytest.mark.parametrize(
        "field,value",
        [("assertion", 123), ("reason", None), ("passed", 1), ("passed", "yes")],
    )
    def test_bad_field_rejected_at_dump(self, field, value):
        with pytest.raises(TypeError) as exc:
            _dump_to_text([_result(**{field: value})])
        assert field in str(exc.value)


class TestVerdictLoadIsUntyped:
    def test_load_returns_plain_dicts(self):
        loaded = _round_trip(_conveyor_results())
        assert all(isinstance(entry, dict) for entry in loaded)
        assert not any(isinstance(entry, AssertionResult) for entry in loaded), (
            "typed reconstruction would require importing relay/verify/, which "
            "verification_path_purity and pipeline_direction_imports both forbid"
        )


class TestVerdictIOErrors:
    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            _load_from_text("{not json")

    def test_non_object_document_raises(self):
        with pytest.raises(ValueError) as exc:
            _load_from_text("[1, 2, 3]")
        assert "not an object" in str(exc.value)

    def test_missing_results_list_raises(self):
        with pytest.raises(ValueError) as exc:
            _load_from_text('{"passed": true}')
        assert "results" in str(exc.value)

    def test_missing_required_key_raises_naming_key_and_index(self):
        text = json.dumps(
            {"results": [{"assertion": "x", "passed": True}], "passed": True}
        )
        with pytest.raises(KeyError) as exc:
            _load_from_text(text)
        assert "reason" in str(exc.value)
        assert "results[0]" in str(exc.value)


class TestVerdictIOPurity:
    def test_verdict_io_imports_stdlib_only(self):
        tree = ast.parse(_VERDICT_IO.read_text())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        offenders = [name for name in imported if name.split(".")[0] == "relay"]
        assert offenders == [], (
            f"relay/verdict_io.py must import stdlib only; found {offenders}. "
            "Importing AssertionResult to annotate a signature keeps every "
            "functional test green while breaking the leaf property."
        )


class TestGoldenVerdict:
    def test_conveyor_verdict_matches_golden(self):
        assert _dump_to_text(_conveyor_results()) == _GOLDEN_VERDICT.read_text()

    def test_golden_verdict_reflects_live_run(self):
        from_disk = load_json(io_module.StringIO(_GOLDEN_VERDICT.read_text()))
        live = [verdict_to_dict(r) for r in _conveyor_results()]
        assert from_disk == live
