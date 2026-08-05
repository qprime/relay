from __future__ import annotations
import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from relay.spec.schema import load_spec
from relay.strategies.plant import get_plant
from relay.trace_io import load_jsonl
from relay.verify.assertions import evaluate_assertion
from tools.plant_server import PlantSession, handle_client

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_BINARY = REPO_ROOT / "host" / "build" / "relay_host_main"
CONVEYOR_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
CONVEYOR_GOLDEN = REPO_ROOT / "specs" / "expectations" / "conveyor_handoff.expected.json"


def _conveyor_session() -> PlantSession:
    spec = load_spec(CONVEYOR_SPEC)
    return PlantSession(get_plant(spec.plant_type)(spec.plant_block))


class TestPlantSession:
    def test_read_actuators_maps_aliases_from_latest_outputs(self):
        session = _conveyor_session()
        result = session.dispatch(
            "read_actuators",
            {"latest_outputs": {"plc_a": {}, "plc_b": {"belt_b_enable": True}}},
        )
        assert result == {"actuators": {"belt_b_enable_signal": True}}

    def test_step_advances_physics_by_injected_dt_only(self):
        session = _conveyor_session()
        first = session.dispatch("step", {"dt_ms": 10.0, "actuators": {}})
        assert set(first["outputs"]) == {"sensor_a_exit_triggered", "part_at_b"}
        assert first["outputs"]["part_at_b"] is False

    def test_route_to_plcs_edge_fires_against_null_prior(self):
        session = _conveyor_session()
        result = session.dispatch(
            "route_to_plcs",
            {
                "current": {"sensor_a_exit_triggered": True, "part_at_b": False},
                "prior": None,
            },
        )
        assert result == {
            "routed": [{"to_plc": "plc_a", "as_key": "sensor_a_exit", "value": True}]
        }

    def test_route_to_plcs_edge_suppressed_when_prior_already_true(self):
        session = _conveyor_session()
        result = session.dispatch(
            "route_to_plcs",
            {
                "current": {"sensor_a_exit_triggered": True, "part_at_b": False},
                "prior": {"sensor_a_exit_triggered": True, "part_at_b": False},
            },
        )
        assert result == {"routed": []}

    def test_unknown_method_raises(self):
        session = _conveyor_session()
        with pytest.raises(ValueError, match="unknown method"):
            session.dispatch("reboot", {})


class TestPlantServerWire:
    def _run_exchange(self, lines: list[str]) -> list[str]:
        async def scenario() -> list[str]:
            spec = load_spec(CONVEYOR_SPEC)
            factory = get_plant(spec.plant_type)
            plant_block = spec.plant_block
            server = await asyncio.start_server(
                lambda r, w: handle_client(r, w, lambda: factory(plant_block)),
                "127.0.0.1",
                0,
            )
            port = server.sockets[0].getsockname()[1]
            responses: list[str] = []
            async with server:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                for line in lines:
                    writer.write((line + "\n").encode())
                    await writer.drain()
                while len(responses) < len(lines):
                    received = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    if not received:
                        break
                    responses.append(received.decode())
                writer.close()
            return responses

        return asyncio.run(scenario())

    def test_responses_echo_request_ids(self):
        responses = self._run_exchange([
            json.dumps({"id": 7, "method": "step", "params": {"dt_ms": 10.0, "actuators": {}}}),
            json.dumps({"id": 9, "method": "route_to_plcs", "params": {
                "current": {"sensor_a_exit_triggered": False, "part_at_b": False},
                "prior": None,
            }}),
        ])
        assert [json.loads(r)["id"] for r in responses] == [7, 9]
        assert all("result" in json.loads(r) for r in responses)

    def test_dispatch_error_is_error_response_not_disconnect(self):
        responses = self._run_exchange([
            json.dumps({"id": 3, "method": "reboot", "params": {}}),
            json.dumps({"id": 4, "method": "step", "params": {"dt_ms": 10.0, "actuators": {}}}),
        ])
        first = json.loads(responses[0])
        assert first["id"] == 3
        assert "unknown method" in first["error"]["message"]
        assert json.loads(responses[1])["id"] == 4

    def test_malformed_line_is_error_then_close(self):
        responses = self._run_exchange(["this is not json"])
        first = json.loads(responses[0])
        assert first["id"] is None
        assert "malformed" in first["error"]["message"]


class TestPlantServerLifecycle:
    def test_server_exits_when_client_disconnects(self):
        server = subprocess.Popen(
            [sys.executable, "-m", "tools.plant_server", str(CONVEYOR_SPEC), "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPO_ROOT,
        )
        try:
            assert server.stdout is not None
            ready = server.stdout.readline().split()
            assert ready and ready[0] == "READY", f"plant server not ready: {ready}"
            with socket.create_connection(("127.0.0.1", int(ready[1]))):
                pass
            assert server.wait(timeout=10) == 0, (
                "the protocol doc promises the server treats client disconnect "
                "as end-of-run and exits"
            )
        finally:
            if server.poll() is None:
                server.kill()
                server.wait(timeout=10)


@pytest.mark.skipif(
    not HOST_BINARY.exists(),
    reason=f"C++ host binary absent at {HOST_BINARY}; build it with "
    "cmake -S host -B host/build && cmake --build host/build",
)
class TestHostAgainstPythonPlantServer:
    def test_remote_socket_plant_reproduces_certified_verdicts(self, tmp_path):
        from tools.emit_host_inputs import emit_host_inputs

        resolved_path, blocks_path = emit_host_inputs(CONVEYOR_SPEC, tmp_path)
        server = subprocess.Popen(
            [sys.executable, "-m", "tools.plant_server", str(CONVEYOR_SPEC), "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=REPO_ROOT,
        )
        try:
            assert server.stdout is not None
            ready = server.stdout.readline().split()
            assert ready and ready[0] == "READY", f"plant server not ready: {ready}"
            port = int(ready[1])

            resolved = json.loads(resolved_path.read_text())
            resolved["plant"]["type"] = "remote_socket"
            resolved["plant"]["config"] = {"endpoint": f"127.0.0.1:{port}"}
            resolved_path.write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")

            trace_path = tmp_path / "cpp_trace.jsonl"
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
                timeout=60,
            )
            with trace_path.open() as stream:
                trace = load_jsonl(stream)
        finally:
            server.terminate()
            server.wait(timeout=10)

        artifact = json.loads(CONVEYOR_GOLDEN.read_text())
        for entry in artifact["assertions"]:
            result = evaluate_assertion(entry["text"], trace)
            assert result.passed == entry["passed"], (
                f"{entry['text']}: python={entry['passed']} "
                f"remote-plant host={result.passed} (reason: {result.reason})"
            )
