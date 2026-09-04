from __future__ import annotations
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from relay.trace_io import load_jsonl
from relay.verify.assertions import evaluate_assertion

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_BINARY = REPO_ROOT / "host" / "build" / "relay_host_main"
ADDRESS_SPEC = REPO_ROOT / "specs" / "conveyor_handoff_address.yaml"
TAG_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
EXPECTATIONS_DIR = REPO_ROOT / "specs" / "expectations"

READ_COILS = 0x01
WRITE_SINGLE_COIL = 0x05

SKIP_REASON = (
    f"C++ host binary absent at {HOST_BINARY}; build it with "
    "cmake -S host -B host/build && cmake --build host/build"
)

pytestmark = pytest.mark.skipif(not HOST_BINARY.exists(), reason=SKIP_REASON)


def _emit(spec_path: Path, out_dir: Path) -> tuple[Path, Path]:
    from tools.emit_host_inputs import emit_host_inputs

    return emit_host_inputs(spec_path, out_dir)


class _ModbusServer:
    def __init__(self, spec_path: Path, log_path: Path) -> None:
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.modbus_server",
                str(spec_path),
                "--port",
                "0",
                "--log",
                str(log_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPO_ROOT,
        )
        assert self._process.stdout is not None
        ready = self._process.stdout.readline().split()
        assert ready and ready[0] == "READY", f"modbus server not ready: {ready}"
        self.port = int(ready[1])
        self._log_path = log_path

    def requests(self) -> list[dict]:
        return [
            json.loads(line) for line in self._log_path.read_text().splitlines() if line.strip()
        ]

    def close(self) -> None:
        self._process.terminate()
        self._process.wait(timeout=10)


def _run_host(
    spec_path: Path, out_dir: Path, tag: str, *, comm_port: int | None
) -> subprocess.CompletedProcess:
    resolved_path, blocks_path = _emit(spec_path, out_dir)
    command = [
        str(HOST_BINARY),
        "--spec",
        str(resolved_path),
        "--st-blocks",
        str(blocks_path),
        "--out",
        str(out_dir / f"trace_{tag}.jsonl"),
    ]
    if comm_port is not None:
        command += ["--comm-endpoint", f"127.0.0.1:{comm_port}"]
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


def _trace(out_dir: Path, tag: str):
    with (out_dir / f"trace_{tag}.jsonl").open() as stream:
        return load_jsonl(stream)


def _artifact(stem: str) -> dict:
    return json.loads((EXPECTATIONS_DIR / f"{stem}.expected.json").read_text())


@pytest.fixture(scope="module")
def tcp_run(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("modbus_loopback")
    server = _ModbusServer(ADDRESS_SPEC, out_dir / "requests.jsonl")
    try:
        completed = _run_host(ADDRESS_SPEC, out_dir, "tcp", comm_port=server.port)
        assert completed.returncode == 0, completed.stderr
        requests = server.requests()
    finally:
        server.close()
    return _trace(out_dir, "tcp"), requests


class TestModbusLoopback:
    def test_address_spec_earns_its_verdicts_over_tcp(self, tcp_run):
        trace, requests = tcp_run
        for entry in _artifact(ADDRESS_SPEC.stem)["assertions"]:
            result = evaluate_assertion(entry["text"], trace)
            assert result.passed == entry["passed"], (
                f"{entry['text']}: expected={entry['passed']} "
                f"over-tcp={result.passed} ({result.reason})"
            )
        writes = [r for r in requests if r["function"] == WRITE_SINGLE_COIL]
        reads = [r for r in requests if r["function"] == READ_COILS]
        assert writes, (
            "the verdicts alone cannot tell a Modbus run from an in-process one; "
            "the server's request log is what proves the bytes moved"
        )
        assert reads, "the consumer must poll its coil at scan top"
        assert {r["address"] for r in writes} == {0}
        assert {r["address"] for r in reads} == {0}
        assert any(r["value"] is True for r in writes)

    def test_verdicts_match_the_in_process_run(self, tmp_path):
        completed = _run_host(ADDRESS_SPEC, tmp_path, "inproc", comm_port=None)
        assert completed.returncode == 0, completed.stderr
        in_process = _trace(tmp_path, "inproc")

        server = _ModbusServer(ADDRESS_SPEC, tmp_path / "requests.jsonl")
        try:
            completed = _run_host(ADDRESS_SPEC, tmp_path, "tcp", comm_port=server.port)
            assert completed.returncode == 0, completed.stderr
        finally:
            server.close()
        over_tcp = _trace(tmp_path, "tcp")

        for text in (entry["text"] for entry in _artifact(ADDRESS_SPEC.stem)["assertions"]):
            expected = evaluate_assertion(text, in_process)
            observed = evaluate_assertion(text, over_tcp)
            assert observed.passed == expected.passed, (
                f"{text}: in-process={expected.passed} over-tcp={observed.passed} "
                f"({observed.reason})"
            )

    def test_verdicts_match_the_tag_spec(self, tcp_run):
        trace, _ = tcp_run
        tag_entries = _artifact(TAG_SPEC.stem)["assertions"]
        address_entries = _artifact(ADDRESS_SPEC.stem)["assertions"]
        assert [e["text"] for e in tag_entries] == [e["text"] for e in address_entries]
        for entry in tag_entries:
            result = evaluate_assertion(entry["text"], trace)
            assert result.passed == entry["passed"], (
                f"{entry['text']}: tag={entry['passed']} address-over-tcp="
                f"{result.passed} ({result.reason})"
            )

    def test_causes_attribution_names_the_declared_producer(self, tcp_run):
        trace, _ = tcp_run
        result = evaluate_assertion("CAUSES(handoff_signal, belt_b_enable)", trace)
        assert result.passed, result.reason
        assert result.attribution is not None
        assert result.attribution.cause_sender == "plc_a", (
            "Modbus carries no sender field; the register map is what makes the "
            f"receipt attributable ({result.attribution})"
        )
        assert result.attribution.effect_plc == "plc_b"

    def test_server_death_mid_run_is_a_diagnosable_failure(self, tmp_path):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def serve_then_die() -> None:
            connection, _ = listener.accept()
            connection.recv(12)
            connection.close()
            listener.close()

        thread = threading.Thread(target=serve_then_die, daemon=True)
        thread.start()
        completed = _run_host(ADDRESS_SPEC, tmp_path, "dead", comm_port=port)
        thread.join(timeout=10)

        assert completed.returncode == 1
        assert "run halted" in completed.stderr, completed.stderr
        assert "modbus_tcp" in completed.stderr, completed.stderr
        assert (tmp_path / "trace_dead.jsonl").exists(), (
            "the trace must retain every record up to the failure"
        )

    def test_tag_spec_with_comm_endpoint_is_rejected(self, tmp_path):
        completed = _run_host(TAG_SPEC, tmp_path, "tag", comm_port=1)
        assert completed.returncode == 1
        assert "no register binding" in completed.stderr, completed.stderr
        assert "cannot connect" not in completed.stderr, (
            "the check reads the bindings, not the strategy name, and it must "
            f"run before the socket does: {completed.stderr}"
        )
