from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any

import pytest

from relay.clock import SimClock
from relay.io_image import IOImage
from relay.runtime.comm import CommBus, CommBuffer
from relay.runtime.fb import FunctionBlock
from relay.runtime.plc import FBExecutor, PLCCoroutine
from relay.trace import TraceLog
from relay.verify.assertions import evaluate_assertion
from relay.plant.conveyor import ConveyorConfig, ConveyorPlant
from relay.spec.schema import load_spec


SCAN_PERIOD_MS = 10.0
MAX_SCANS = 100

_PLC_A_ST = """
IF sensor_a_exit AND NOT handoff_signaled THEN
handoff_signaled := TRUE;
handoff_signal := TRUE;
END_IF;
"""

_PLC_B_ST = """
IF handoff_signal AND NOT belt_b_enable THEN
belt_b_enable := TRUE;
END_IF;
"""


def _wire(fb: FunctionBlock, outputs: dict[str, str]) -> FBExecutor:
    last: dict[str, Any] = {}

    def _exec(
        io: IOImage, comm: CommBuffer, clock: SimClock, dt_ms: float
    ) -> tuple[IOImage, list[tuple[str, str, Any]]]:
        produced, outgoing = fb.scan(io, comm, clock, dt_ms)
        for key, target in outputs.items():
            value = produced.get(key)
            if value and not last.get(key):
                outgoing.append((target, key, value))
            last[key] = value
        return produced, outgoing
    return _exec


def _default_executors() -> dict[str, FBExecutor]:
    return {
        "plc_a": _wire(FunctionBlock(source=_PLC_A_ST), {"handoff_signal": "plc_b"}),
        "plc_b": _wire(FunctionBlock(source=_PLC_B_ST), {}),
    }


async def _simulate(
    executors: dict[str, FBExecutor],
    max_scans: int,
) -> TraceLog:
    bus = CommBus()
    for plc_id in executors:
        bus.register(plc_id)

    trace = TraceLog()
    plant = ConveyorPlant(
        ConveyorConfig(
            belt_speed_m_per_s=0.5,
            sensor_trigger_threshold_m=0.1,
            actuator_latency_ms=50.0,
        )
    )

    clock_queues: dict[str, asyncio.Queue[SimClock]] = {
        plc_id: asyncio.Queue() for plc_id in executors
    }
    done_queue: asyncio.Queue[None] = asyncio.Queue()

    tasks = [
        asyncio.create_task(
            PLCCoroutine(
                plc_id=plc_id,
                executor=executor,
                scan_period_ms=SCAN_PERIOD_MS,
            ).run(clock_queues[plc_id], bus, trace, max_scans, done_queue)
        )
        for plc_id, executor in executors.items()
    ]

    clock = SimClock.zero()
    belt_b_state: dict[str, bool] = {"enable": False}

    for _ in range(max_scans):
        plant_out = plant.step(SCAN_PERIOD_MS, belt_b_state["enable"])

        if plant_out.sensor_a_exit_triggered:
            await bus.send("plc_a", "sensor_a_exit", True)
        if plant_out.part_at_b:
            await bus.send("plc_b", "part_at_b", True)

        for q in clock_queues.values():
            await q.put(clock)

        for _ in executors:
            await done_queue.get()

        if not belt_b_state["enable"]:
            for rec in trace.for_plc("plc_b"):
                if rec.outputs.get("belt_b_enable"):
                    belt_b_state["enable"] = True
                    break

        clock = clock.advance(SCAN_PERIOD_MS)

    await asyncio.gather(*tasks)
    return trace


def _run_simulation(
    plc_a_executor: FBExecutor | None = None,
    plc_b_executor: FBExecutor | None = None,
    max_scans: int = MAX_SCANS,
) -> TraceLog:
    executors = _default_executors()
    if plc_a_executor is not None:
        executors["plc_a"] = plc_a_executor
    if plc_b_executor is not None:
        executors["plc_b"] = plc_b_executor
    return asyncio.run(_simulate(executors, max_scans))


class TestConveyorHandoff:
    def test_part_arrives_at_b_within_timeout(self):
        trace = _run_simulation()
        result = evaluate_assertion("EVENTUALLY(part_at_b, within: 500ms)", trace)
        assert result.passed, result.reason

    def test_handoff_signal_precedes_belt_b_enable(self):
        trace = _run_simulation()
        result = evaluate_assertion("PRECEDES(handoff_signal, belt_b_enable)", trace)
        assert result.passed, result.reason

    def test_part_never_arrives_when_sensor_a_never_triggers(self):
        def dead_plc_a(
            io: IOImage, comm: CommBuffer, clock: SimClock, dt_ms: float
        ) -> tuple[IOImage, list]:
            return IOImage.empty(), []

        trace = _run_simulation(plc_a_executor=dead_plc_a)
        result = evaluate_assertion("EVENTUALLY(part_at_b, within: 500ms)", trace)
        assert not result.passed, "expected failure when plc_a never signals handoff"

    def test_scan_loop_io_image_immutable_during_execution(self):
        captured: list[IOImage] = []

        def capturing_executor(
            io: IOImage, comm: CommBuffer, clock: SimClock, dt_ms: float
        ) -> tuple[IOImage, list]:
            captured.append(io)
            return IOImage(values={"counter": io.get("counter", 0) + 1}), []

        trace = _run_simulation(plc_a_executor=capturing_executor, max_scans=5)

        for captured_io in captured:
            with pytest.raises(TypeError):
                captured_io.values["hacked"] = True  # type: ignore[index]

        for rec in trace.for_plc("plc_a"):
            assert "hacked" not in rec.io.values
            assert "hacked" not in rec.outputs.values

    def test_clock_is_external(self):
        def clock_capturing_executor(
            io: IOImage, comm: CommBuffer, clock: SimClock, dt_ms: float
        ) -> tuple[IOImage, list]:
            return IOImage(values={"sim_tick": clock.tick}), []

        trace = _run_simulation(plc_a_executor=clock_capturing_executor, max_scans=5)

        ticks = [r.outputs.get("sim_tick") for r in trace.for_plc("plc_a")]
        assert ticks == list(range(5)), f"sim ticks not externally driven: {ticks}"


class TestSpecLoading:
    def test_spec_loads_from_yaml(self):
        spec_path = Path(__file__).parent.parent / "specs" / "conveyor_handoff.yaml"
        spec = load_spec(spec_path)
        assert spec.system_name == "conveyor_handoff"
        assert {p["id"] for p in spec.plcs} == {"plc_a", "plc_b"}
        assert "EVENTUALLY(part_at_b, within: 500ms)" in spec.assertions
        assert "PRECEDES(handoff_signal, belt_b_enable)" in spec.assertions
