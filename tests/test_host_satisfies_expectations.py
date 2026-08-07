from __future__ import annotations
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from relay.io_image import IOImage
from relay.trace import TraceLog
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


def _spawn_plant_server() -> tuple[subprocess.Popen, int]:
    server = subprocess.Popen(
        [sys.executable, "-m", "tools.plant_server", str(CONVEYOR_SPEC), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
    )
    assert server.stdout is not None
    ready = server.stdout.readline().split()
    assert ready and ready[0] == "READY", f"plant server not ready: {ready}"
    return server, int(ready[1])


def _delay_signal_activation(trace: TraceLog, plc_id: str, signal: str, delay_scans: int) -> TraceLog:
    per_plc = sorted(
        (r for r in trace.records if r.plc_id == plc_id), key=lambda r: r.clock.tick
    )
    io_history = {r.clock.tick: bool(r.io.get(signal, False)) for r in per_plc}
    out_history = {
        r.clock.tick: bool(r.outputs.values.get(signal, False)) for r in per_plc
    }

    def shifted(record):
        tick = record.clock.tick
        io_values = dict(record.io.values)
        out_values = dict(record.outputs.values)
        if signal in io_values:
            io_values[signal] = io_history.get(tick - delay_scans, False)
        if signal in out_values:
            out_values[signal] = out_history.get(tick - delay_scans, False)
        return replace(
            record, io=IOImage(values=io_values), outputs=IOImage(values=out_values)
        )

    return TraceLog([
        shifted(r) if r.plc_id == plc_id else r for r in trace.records
    ])


class TestHostSatisfiesExpectations:
    def test_verdicts_match_per_assertion(self, cpp_trace, artifact):
        for entry in artifact["assertions"]:
            cpp = evaluate_assertion(entry["text"], cpp_trace)
            assert cpp.passed == entry["passed"], (
                f"{entry['text']}: python={entry['passed']} cpp={cpp.passed} "
                f"(python witness: {entry['witness']}; cpp reason: {cpp.reason})"
            )

    @pytest.mark.parametrize("plant_mode", ["conveyor", "remote_socket"])
    def test_gate_holds_across_ten_consecutive_runs(self, artifact, tmp_path, plant_mode):
        from tools.emit_host_inputs import emit_host_inputs

        resolved_path, blocks_path = emit_host_inputs(CONVEYOR_SPEC, tmp_path)
        for attempt in range(10):
            trace_path = tmp_path / f"cpp_trace_{plant_mode}_{attempt}.jsonl"
            command = [
                str(HOST_BINARY),
                "--spec", str(resolved_path),
                "--st-blocks", str(blocks_path),
                "--out", str(trace_path),
            ]
            server = None
            if plant_mode == "remote_socket":
                server, port = _spawn_plant_server()
                command += ["--plant-endpoint", f"127.0.0.1:{port}"]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            finally:
                if server is not None:
                    server.terminate()
                    server.wait(timeout=10)
            with trace_path.open() as stream:
                trace = load_jsonl(stream)
            for entry in artifact["assertions"]:
                result = evaluate_assertion(entry["text"], trace)
                assert result.passed == entry["passed"], (
                    f"run {attempt + 1}/10 ({plant_mode}): {entry['text']}: "
                    f"python={entry['passed']} cpp={result.passed} "
                    f"(reason: {result.reason}); a single intermittent failure "
                    "is a defect to diagnose, not a retry to absorb"
                )

    def test_causes_holds_under_injected_skew(self, cpp_trace):
        skewed = _delay_signal_activation(
            cpp_trace, "plc_b", "belt_b_enable", delay_scans=60
        )
        precedes = evaluate_assertion(
            "PRECEDES(handoff_signal, belt_b_enable, within: 50ms)", skewed
        )
        causes = evaluate_assertion("CAUSES(handoff_signal, belt_b_enable)", skewed)
        assert not precedes.passed, (
            f"600ms of injected skew must exhaust the 50ms budget: {precedes.reason}"
        )
        assert causes.passed, (
            "CAUSES reads no clock on the pass/fail path and must survive delays "
            f"that fail PRECEDES: {causes.reason}"
        )

    def test_skips_cleanly_when_cpp_binary_absent(self):
        assert str(HOST_BINARY) in SKIP_REASON
        assert "cmake" in SKIP_REASON
