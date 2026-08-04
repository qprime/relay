from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

from relay.trace_io import load_jsonl
from relay.verify.assertions import evaluate_assertion

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_BINARY = REPO_ROOT / "host" / "build" / "relay_host_main"
CONVEYOR_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
CONVEYOR_GOLDEN = REPO_ROOT / "specs" / "expectations" / "conveyor_handoff.expected.json"

SKIP_REASON = (
    f"C++ host binary absent at {HOST_BINARY}; build it with "
    "cmake -S host -B host/build && cmake --build host/build"
)

pytestmark = pytest.mark.skipif(not HOST_BINARY.exists(), reason=SKIP_REASON)


@pytest.fixture(scope="module")
def cpp_trace(tmp_path_factory):
    from tools.emit_host_inputs import emit_host_inputs

    out_dir = tmp_path_factory.mktemp("host_inputs")
    resolved_path, blocks_path = emit_host_inputs(CONVEYOR_SPEC, out_dir)
    trace_path = out_dir / "cpp_trace.jsonl"
    subprocess.run(
        [
            str(HOST_BINARY),
            "--spec", str(resolved_path),
            "--st-blocks", str(blocks_path),
            "--out", str(trace_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    with trace_path.open() as stream:
        return load_jsonl(stream)


@pytest.fixture(scope="module")
def artifact():
    return json.loads(CONVEYOR_GOLDEN.read_text())


def _first_true_ms(trace, signal: str) -> float | None:
    for record in trace.records:
        if signal in record.outputs.values:
            value = record.outputs.values[signal]
        else:
            value = record.io.get(signal)
        if value:
            return record.clock.elapsed_ms
    return None


class TestHostSatisfiesExpectations:
    def test_verdicts_match_per_assertion(self, cpp_trace, artifact):
        for entry in artifact["assertions"]:
            cpp = evaluate_assertion(entry["text"], cpp_trace)
            assert cpp.passed == entry["passed"], (
                f"{entry['text']}: python={entry['passed']} cpp={cpp.passed} "
                f"(python witness: {entry['witness']}; cpp reason: {cpp.reason})"
            )

    def test_cpp_trace_reproduces_precedes_same_scan(self, cpp_trace):
        handoff_ms = _first_true_ms(cpp_trace, "handoff_signal")
        belt_ms = _first_true_ms(cpp_trace, "belt_b_enable")
        assert handoff_ms is not None
        assert belt_ms is not None
        assert handoff_ms == belt_ms, (
            f"handoff_signal first true at {handoff_ms}ms but belt_b_enable at "
            f"{belt_ms}ms; in sim both land in the same scan"
        )

    def test_skips_cleanly_when_cpp_binary_absent(self):
        assert str(HOST_BINARY) in SKIP_REASON
        assert "cmake" in SKIP_REASON
