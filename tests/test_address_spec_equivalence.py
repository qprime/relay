from __future__ import annotations
import asyncio
import json
from pathlib import Path

import pytest

from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import TaskSpec, load_spec
from relay.trace import TraceLog
from relay.verify.assertions import evaluate_assertion

REPO_ROOT = Path(__file__).resolve().parents[1]
TAG_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
ADDRESS_SPEC = REPO_ROOT / "specs" / "conveyor_handoff_address.yaml"
TAG_EXPECTED = REPO_ROOT / "specs" / "expectations" / "conveyor_handoff.expected.json"
ADDRESS_EXPECTED = REPO_ROOT / "specs" / "expectations" / "conveyor_handoff_address.expected.json"
MAX_SCANS = 100


def _run(spec: TaskSpec) -> TraceLog:
    return asyncio.run(simulate(spec, compile_st_blocks(spec), max_scans=MAX_SCANS))


@pytest.fixture(scope="module")
def tag_spec() -> TaskSpec:
    return load_spec(TAG_SPEC)


@pytest.fixture(scope="module")
def address_spec() -> TaskSpec:
    return load_spec(ADDRESS_SPEC)


class TestAddressSpecEquivalence:
    def test_generated_st_is_byte_identical(self, tag_spec, address_spec):
        tag_blocks = compile_st_blocks(tag_spec)
        address_blocks = compile_st_blocks(address_spec)
        assert tag_blocks == address_blocks
        assert compile_st_blocks(address_spec) == address_blocks

    def test_verdicts_match_the_tag_spec(self, tag_spec, address_spec):
        tag_trace = _run(tag_spec)
        address_trace = _run(address_spec)
        assert tag_spec.assertions == address_spec.assertions
        for text in tag_spec.assertions:
            tag_result = evaluate_assertion(text, tag_trace)
            address_result = evaluate_assertion(text, address_trace)
            assert address_result.passed == tag_result.passed, (
                f"{text}: tag={tag_result.passed} address={address_result.passed} "
                f"(address reason: {address_result.reason})"
            )

    def test_expectations_artifacts_agree_per_assertion(self):
        tag_artifact = json.loads(TAG_EXPECTED.read_text())
        address_artifact = json.loads(ADDRESS_EXPECTED.read_text())
        tag_entries = tag_artifact["assertions"]
        address_entries = address_artifact["assertions"]
        assert len(tag_entries) == len(address_entries)
        for tag_entry, address_entry in zip(tag_entries, address_entries):
            assert address_entry["text"] == tag_entry["text"]
            assert address_entry["passed"] == tag_entry["passed"], tag_entry["text"]

    def test_address_never_reaches_the_trace(self, tag_spec, address_spec):
        def trace_keys(trace: TraceLog) -> set[str]:
            keys: set[str] = set()
            for record in trace.records:
                keys.update(record.io.values)
                keys.update(record.outputs.values)
                keys.update(record.sends)
                keys.update(record.recvs)
            return keys

        assert trace_keys(_run(address_spec)) == trace_keys(_run(tag_spec))
