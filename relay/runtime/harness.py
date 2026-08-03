from __future__ import annotations
import asyncio
import warnings
from typing import Any

import relay.plant  # noqa: F401  -- ensure plant registrations are loaded
from relay.clock import DEFAULT_SCAN_PERIOD_MS, SimClock
from relay.io_image import IOImage
from relay.runtime.comm import CommBus
from relay.runtime.fb import FunctionBlock
from relay.runtime.plc import PLCCoroutine
from relay.spec.schema import TaskSpec
from relay.strategies.comm import build_comm_strategy
from relay.strategies.plant import get_plant
from relay.trace import TraceLog


def _warn_on_subscan_timings(spec: TaskSpec, scan_period_ms: float) -> None:
    for plc_id, entry in (spec.behavior or {}).items():
        if not isinstance(entry, dict):
            continue
        for trigger in entry.get("triggers") or []:
            if not isinstance(trigger, dict):
                continue
            where = f"Behavior.{plc_id}.triggers[{trigger.get('id')}]"
            debounce = (trigger.get("when") or {}).get("debounce_ms") or 0
            if 0 < debounce < scan_period_ms:
                warnings.warn(
                    f"{where}.when.debounce_ms is {debounce}ms, shorter than the "
                    f"{scan_period_ms:g}ms scan period; timers advance only at scan "
                    "boundaries, so this debounce is a no-op",
                    UserWarning,
                )
            duration = (trigger.get("emit") or {}).get("duration_ms") or 0
            if 0 < duration < scan_period_ms:
                warnings.warn(
                    f"{where}.emit.duration_ms is {duration}ms, shorter than the "
                    f"{scan_period_ms:g}ms scan period; the pulse cannot deassert "
                    "before the next scan boundary",
                    UserWarning,
                )


async def simulate(
    spec: TaskSpec,
    st_blocks: dict[str, str],
    *,
    max_scans: int = 100,
    scan_period_ms: float = DEFAULT_SCAN_PERIOD_MS,
) -> TraceLog:
    plc_ids = spec.plc_ids
    block_ids = set(st_blocks)
    missing = set(plc_ids) - block_ids
    if missing:
        raise ValueError(f"missing ST blocks for plc_ids: {sorted(missing)}")

    _warn_on_subscan_timings(spec, scan_period_ms)

    plant_factory = get_plant(spec.plant_type)
    plant = plant_factory(spec.plant_block)
    comm_strategy = build_comm_strategy(spec.comm_strategy, spec.comm_block)

    bus = CommBus()
    trace = TraceLog()
    for pid in plc_ids:
        bus.register(pid)

    function_blocks: dict[str, FunctionBlock] = {
        pid: FunctionBlock(source=st_blocks[pid], plc_ids=plc_ids) for pid in plc_ids
    }

    latest_outputs: dict[str, IOImage] = {pid: IOImage.empty() for pid in plc_ids}
    prior_outputs: dict[str, IOImage] = {pid: IOImage.empty() for pid in plc_ids}

    clock_queues: dict[str, asyncio.Queue[SimClock]] = {
        pid: asyncio.Queue() for pid in plc_ids
    }
    done_queue: asyncio.Queue[None] = asyncio.Queue()

    def _capturing_executor(plc_id: str):
        fb = function_blocks[plc_id]

        def _exec(io: IOImage, comm, clock: SimClock, dt_ms: float):
            outputs, outgoing = fb.scan(io, comm, clock, dt_ms)
            latest_outputs[plc_id] = outputs
            return outputs, outgoing

        return _exec

    tasks = [
        asyncio.create_task(
            PLCCoroutine(
                plc_id=pid,
                executor=_capturing_executor(pid),
                scan_period_ms=scan_period_ms,
            ).run(clock_queues[pid], bus, trace, max_scans, done_queue)
        )
        for pid in plc_ids
    ]

    clock = SimClock.zero()
    prior_plant_out: Any | None = None

    async def _await_done_or_failure() -> None:
        get_task = asyncio.create_task(done_queue.get())
        done, pending = await asyncio.wait(
            [get_task, *tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if get_task in done:
            get_task.result()
            return
        get_task.cancel()
        for t in done:
            if t.exception() is not None:
                raise t.exception()  # type: ignore[misc]
        raise AssertionError(
            "PLC task exited cleanly before harness loop completed; "
            "PLCCoroutine.max_scans and harness max_scans must match"
        )

    try:
        for _ in range(max_scans):
            actuator_state = plant.read_actuators(latest_outputs)
            plant_out = plant.step(scan_period_ms, actuator_state)

            for target, key, value in plant.route_to_plcs(plant_out, prior_plant_out):
                await bus.send(target, key, value)

            for q in clock_queues.values():
                await q.put(clock)

            for _ in plc_ids:
                await _await_done_or_failure()

            for pid in plc_ids:
                for target, key, value in comm_strategy.route(
                    pid, latest_outputs[pid], prior_outputs[pid]
                ):
                    await bus.send(target, key, value)

            prior_outputs = {pid: latest_outputs[pid] for pid in plc_ids}
            prior_plant_out = plant_out
            clock = clock.advance(scan_period_ms)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    for t in tasks:
        if t.cancelled():
            continue
        exc = t.exception()
        if exc is not None:
            raise exc
    return trace
