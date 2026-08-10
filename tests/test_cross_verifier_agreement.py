"""Two independently written verifiers, one trace.

`tests/test_host_satisfies_expectations.py` runs the *Python* verifier over the
host's JSONL, so its claim is one verifier applied to two traces — a bug in
`relay/verify/assertions.py` is invisible to it. Here the trace is held fixed
and the verifier is the only variable, which is the whole reason for writing a
second implementation.

Python stays the oracle. A disagreement is a bug to investigate, not a signal to
update an artifact.
"""

from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

from relay.trace_io import dump_jsonl, load_jsonl
from relay.verdict_io import load_json, verdict_to_dict
from relay.verify.assertions import evaluate_all
from tests.test_host_satisfies_expectations import _delay_signal_activation

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_BINARY = REPO_ROOT / "host" / "build" / "relay_host_main"
VERIFY_BINARY = REPO_ROOT / "host" / "build" / "relay_host_verify"
CONVEYOR_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
GOLDEN_TRACE = Path(__file__).parent / "golden" / "conveyor_trace.jsonl"

SKIP_REASON = (
    f"C++ binaries absent at {HOST_BINARY} and {VERIFY_BINARY}; build them with "
    "cmake -S host -B host/build && cmake --build host/build"
)

pytestmark = pytest.mark.skipif(
    not (HOST_BINARY.exists() and VERIFY_BINARY.exists()), reason=SKIP_REASON
)


@pytest.fixture(scope="module")
def host_inputs(tmp_path_factory):
    from tools.emit_host_inputs import emit_host_inputs

    out_dir = tmp_path_factory.mktemp("cross_verifier")
    resolved_path, blocks_path = emit_host_inputs(CONVEYOR_SPEC, out_dir)
    return resolved_path, blocks_path


def _assertions(resolved_path: Path) -> list[str]:
    return json.loads(resolved_path.read_text())["assertions"]


def _run_cpp_verifier(resolved_path: Path, trace_path: Path, out_path: Path):
    """Returns (exit code, verdict entries). `check=False` is load-bearing: a
    failing assertion exits 1, which is the correct answer for a verifier used
    from a shell and would otherwise abort the catcher test."""
    completed = subprocess.run(
        [
            str(VERIFY_BINARY),
            "--spec", str(resolved_path),
            "--trace", str(trace_path),
            "--out", str(out_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode != 2, (
        f"relay_host_verify could not read its inputs: {completed.stderr}"
    )
    with out_path.open() as stream:
        return completed.returncode, load_json(stream)


def _comparable(entry: dict) -> dict:
    """Everything except the witness prose. Comparing `reason` would couple the
    C++ implementation to Python's phrasing, so improving a sentence on one side
    would break the other for no verification gain."""
    return {k: v for k, v in entry.items() if k != "reason"}


def _agree(python_results, cpp_entries) -> None:
    assert len(cpp_entries) == len(python_results)
    for expected, actual in zip(python_results, cpp_entries):
        assert _comparable(verdict_to_dict(expected)) == _comparable(actual), (
            f"verifiers disagree on {expected.assertion!r}\n"
            f"  python: {expected.reason}\n"
            f"  cpp:    {actual['reason']}"
        )


class TestCrossVerifierAgreement:
    def test_cpp_matches_python_on_golden_sim_trace(self, host_inputs, tmp_path):
        """The trace the *Python* runtime produced, verified by both. If C++ only
        ever verified the host's own trace, a host runtime bug and a C++ verifier
        bug that happened to agree would be indistinguishable from correctness."""
        resolved_path, _ = host_inputs
        with GOLDEN_TRACE.open() as stream:
            trace = load_jsonl(stream)
        python_results = evaluate_all(_assertions(resolved_path), trace)
        assert python_results, "conveyor spec must declare assertions"

        code, cpp_entries = _run_cpp_verifier(
            resolved_path, GOLDEN_TRACE, tmp_path / "verdict.json"
        )
        _agree(python_results, cpp_entries)
        assert code == 0

    def test_cpp_matches_python_on_host_trace(self, host_inputs, tmp_path):
        resolved_path, blocks_path = host_inputs
        trace_path = tmp_path / "host_trace.jsonl"
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
            timeout=120,
        )
        with trace_path.open() as stream:
            trace = load_jsonl(stream)
        python_results = evaluate_all(_assertions(resolved_path), trace)

        code, cpp_entries = _run_cpp_verifier(
            resolved_path, trace_path, tmp_path / "verdict.json"
        )
        _agree(python_results, cpp_entries)
        assert code == 0

    def test_perturbed_trace_flips_both_verifiers(self, host_inputs, tmp_path):
        """The catcher. A C++ verifier stubbed to echo the expectations artifact,
        or one reporting a hardcoded verdict, passes every other test here and
        fails this one."""
        resolved_path, _ = host_inputs
        with GOLDEN_TRACE.open() as stream:
            trace = load_jsonl(stream)
        skewed = _delay_signal_activation(
            trace, "plc_b", "belt_b_enable", delay_scans=60
        )
        trace_path = tmp_path / "skewed_trace.jsonl"
        with trace_path.open("w") as stream:
            dump_jsonl(skewed, stream)

        python_results = evaluate_all(_assertions(resolved_path), skewed)
        precedes = [r for r in python_results if r.assertion.startswith("PRECEDES")]
        assert precedes and not precedes[0].passed, (
            "600ms of injected skew must exhaust the 50ms budget for this test to "
            f"be measuring anything: {precedes[0].reason if precedes else 'absent'}"
        )

        code, cpp_entries = _run_cpp_verifier(
            resolved_path, trace_path, tmp_path / "verdict.json"
        )
        _agree(python_results, cpp_entries)
        assert code == 1, "relay_host_verify exits 1 when any assertion fails"

    def test_host_exits_nonzero_when_trace_ring_drops(self, host_inputs, tmp_path):
        """A dropped prefix moves every first-occurrence anchor later, so the
        verdict is wrong in the direction that gets believed. The trace is still
        written, so the failure stays diagnosable."""
        resolved_path, blocks_path = host_inputs
        trace_path = tmp_path / "truncated_trace.jsonl"
        completed = subprocess.run(
            [
                str(HOST_BINARY),
                "--spec", str(resolved_path),
                "--st-blocks", str(blocks_path),
                "--out", str(trace_path),
                "--trace-capacity", "8",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 1, (
            "a truncated trace must not exit 0; a warning was adequate when the "
            "output was for a human, not when it feeds a verdict"
        )
        assert "trace ring dropped" in completed.stderr
        assert trace_path.exists(), "the partial trace is still written"

    def test_verify_binary_rejects_a_missing_trace_with_exit_two(
        self, host_inputs, tmp_path
    ):
        resolved_path, _ = host_inputs
        completed = subprocess.run(
            [
                str(VERIFY_BINARY),
                "--spec", str(resolved_path),
                "--trace", str(tmp_path / "absent.jsonl"),
                "--out", str(tmp_path / "verdict.json"),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 2, (
            "exit 2 is reserved for usage and load errors so a caller can tell "
            "'the run did not hold' from 'I could not read it'"
        )

    def test_skips_cleanly_when_cpp_binaries_absent(self):
        assert str(VERIFY_BINARY) in SKIP_REASON
        assert "cmake" in SKIP_REASON
