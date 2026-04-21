from __future__ import annotations
import asyncio
import time
from typing import Any

from relay.runtime.clock import SimClock
from relay.runtime.comm import CommBus, CommBuffer
from relay.runtime.plc import IOImage, FBExecutor
from relay.verify.trace import TraceLog, ScanRecord
from relay.verify.assertions import evaluate_assertion
from relay.plant.conveyor import ConveyorConfig, ConveyorPlant


SCAN_PERIOD_MS = 10.0
MAX_SCANS = 100


def _plc_a_executor(io: IOImage, comm: CommBuffer, clock: SimClock) -> tuple[IOImage, list[tuple[str, str, Any]]]:
    sensor_triggered = io.get("sensor_a_exit", False)
    already_signaled = io.get("handoff_signaled", False)
    outgoing: list[tuple[str, str, Any]] = []

    if sensor_triggered and not already_signaled:
        outgoing.append(("plc_b", "handoff_signal", True))
        io = io.with_value("handoff_signaled", True)
        io = io.with_value("handoff_signal", True)

    return io, outgoing


def _plc_b_executor(io: IOImage, comm: CommBuffer, clock: SimClock) -> tuple[IOImage, list[tuple[str, str, Any]]]:
    handoff = io.get("handoff_signal", False) or comm.pending.get("handoff_signal", False)
    already_enabled = io.get("belt_b_enable", False)

    if handoff and not already_enabled:
        io = io.with_value("belt_b_enable", True)

    return io, []


def _run_scan(
    clock: SimClock,
    bus: CommBus,
    trace: TraceLog,
    executors: dict[str, FBExecutor],
    io_state: dict[str, IOImage],
) -> dict[str, IOImage]:
    new_io_state: dict[str, IOImage] = {}

    for plc_id, executor in executors.items():
        io = io_state[plc_id]

        comm_buf = asyncio.get_event_loop().run_until_complete(bus.drain(plc_id))
        promoted, _ = comm_buf.promote()
        for key, value in promoted.items():
            io = io.with_value(key, value)

        snapshot = io
        new_io, outgoing = executor(snapshot, comm_buf, clock)

        for target_plc, key, value in outgoing:
            asyncio.get_event_loop().run_until_complete(bus.send(target_plc, key, value))

        trace.record(ScanRecord(plc_id=plc_id, clock=clock, io=snapshot, outputs=new_io))
        new_io_state[plc_id] = new_io

    return new_io_state


def _run_simulation(
    plc_a_executor: FBExecutor = _plc_a_executor,
    plc_b_executor: FBExecutor = _plc_b_executor,
    max_scans: int = MAX_SCANS,
) -> TraceLog:
    bus = CommBus()
    bus.register("plc_a")
    bus.register("plc_b")

    trace = TraceLog()

    plant = ConveyorPlant(
        ConveyorConfig(
            belt_speed_m_per_s=0.5,
            sensor_trigger_threshold_m=0.1,
            actuator_latency_ms=50.0,
        )
    )

    executors: dict[str, FBExecutor] = {"plc_a": plc_a_executor, "plc_b": plc_b_executor}
    io_state: dict[str, IOImage] = {plc_id: IOImage.empty() for plc_id in executors}
    clock = SimClock.zero()

    loop = asyncio.new_event_loop()

    def _drain(plc_id: str) -> CommBuffer:
        return loop.run_until_complete(bus.drain(plc_id))

    def _send(to: str, key: str, value: Any) -> None:
        loop.run_until_complete(bus.send(to, key, value))

    for _ in range(max_scans):
        belt_b_enable = bool(io_state["plc_b"].get("belt_b_enable", False))
        plant_out = plant.step(SCAN_PERIOD_MS, belt_b_enable)

        if plant_out.sensor_a_exit_triggered:
            _send("plc_a", "sensor_a_exit", True)
        if plant_out.part_at_b:
            _send("plc_b", "part_at_b", True)

        new_io_state: dict[str, IOImage] = {}
        for plc_id, executor in executors.items():
            io = io_state[plc_id]

            comm_buf = _drain(plc_id)
            promoted, _ = comm_buf.promote()
            for key, value in promoted.items():
                io = io.with_value(key, value)

            snapshot = io
            new_io, outgoing = executor(snapshot, comm_buf, clock)

            for target_plc, key, value in outgoing:
                _send(target_plc, key, value)

            trace.record(ScanRecord(plc_id=plc_id, clock=clock, io=snapshot, outputs=new_io))
            new_io_state[plc_id] = new_io

        io_state = new_io_state
        clock = clock.advance(SCAN_PERIOD_MS)

    loop.close()
    return trace


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
        def dead_plc_a(io: IOImage, comm: CommBuffer, clock: SimClock) -> tuple[IOImage, list]:
            return io, []

        trace = _run_simulation(plc_a_executor=dead_plc_a)
        result = evaluate_assertion("EVENTUALLY(part_at_b, within: 500ms)", trace)
        assert not result.passed, "expected failure when plc_a never signals handoff"

    def test_scan_loop_io_image_immutable_during_execution(self):
        snapshots: list[IOImage] = []
        outputs: list[IOImage] = []

        def capturing_executor(
            io: IOImage, comm: CommBuffer, clock: SimClock
        ) -> tuple[IOImage, list]:
            snapshots.append(IOImage(values=dict(io.values)))
            new_io = io.with_value("counter", io.get("counter", 0) + 1)
            outputs.append(new_io)
            return new_io, []

        _run_simulation(plc_a_executor=capturing_executor, max_scans=5)

        for i, (snap, out) in enumerate(zip(snapshots, outputs)):
            assert snap.values != out.values or i == 0, (
                f"scan {i}: snapshot was mutated — io_image was not immutable during execution"
            )

    def test_clock_is_external(self):
        wall_times: list[float] = []

        def clock_capturing_executor(
            io: IOImage, comm: CommBuffer, clock: SimClock
        ) -> tuple[IOImage, list]:
            wall_times.append(time.monotonic())
            return io.with_value("sim_tick", clock.tick), []

        trace = _run_simulation(plc_a_executor=clock_capturing_executor, max_scans=5)

        ticks = [r.outputs.get("sim_tick") for r in trace.for_plc("plc_a")]
        assert ticks == list(range(5)), f"sim ticks not externally driven: {ticks}"

        elapsed_spread = max(wall_times) - min(wall_times)
        assert elapsed_spread < 1.0, "simulation took wall-clock time — clock may not be injected"
