# Invariant: SimClock is the only time source for execution-path code

**Status:** Active | **As-Of:** 2026-04-21 | **Scope:** `relay/runtime/`, `relay/plant/`, `relay/st/`, comm strategies

## Statement

Any code reachable from a PLC executor — `FBExecutor` implementations, plant
`step` methods, the ST interpreter, comm strategies, and anything they call —
derives time only from the injected `SimClock` or the `dt_ms` parameter the
harness passes in. No execution-path module may import `time`, `datetime`,
`asyncio.sleep` (for delay), `loop.time()`, `perf_counter`, `monotonic`, or
any other source of wall-clock or monotonic time.

The harness itself drives `SimClock` advancement. The execution path consumes
clock values; it does not produce them.

## Why

Determinism and replayability are the simulator's contract with verification.
The same task spec, the same generated ST, the same plant config must produce
the same trace — bit-identical, scan-by-scan — on any machine, any run. That
is what makes assertions stable and what makes a failing trace something a
human or classifier can actually debug instead of chasing flake.

Any wall-clock read in the execution path silently breaks this. Two runs on
the same machine can differ if one happens to schedule across a context
switch the other doesn't. A `time.sleep(0.001)` "to let the network catch up"
introduces real wall-clock dependence into the simulated clock domain. A
`datetime.now()` in a logging path that gets folded into the trace makes
the trace different on every run.

The corruption is hard to catch. Most timing tests run with loose tolerances
("EVENTUALLY within 500 ms"), so a 5 ms wall-clock drift slides under the
radar. The replay capability silently degrades — traces from yesterday no
longer reproduce — without any test going red.

## What this looks like

1. **`SimClock` is constructed by the harness** ([relay/clock.py](../../relay/clock.py)),
   advanced by the harness, and passed into each PLC's executor as the
   `clock` argument.
2. **`dt_ms` is the only other time-flowing input** to the executor.
   It is what the harness chose for this scan period; the executor takes
   it as given.
3. **The ST interpreter advances all timer state from the injected `dt_ms`**
   ([relay/st/interpreter.py](../../relay/st/interpreter.py)), not from a
   clock read. TON timer accumulators tick by `dt_ms`. `STContext` does not
   hold its own elapsed-time field; if execution-path code needs elapsed
   time it reads `SimClock.elapsed_ms` from the injected clock.
4. **Plant models advance physics by the injected `elapsed_ms`** parameter
   ([relay/plant/conveyor.py:50](../../relay/plant/conveyor.py#L50)). They
   do not measure how long their own `step()` took.
5. **No `import time`, `import datetime`, `from time import *`** in any
   module under `relay/runtime/`, `relay/plant/`, `relay/st/`, or comm
   strategy modules. `asyncio.sleep` is permitted only in the harness for
   yielding to other coroutines, never for "pacing" the simulation.

## What violates this invariant

- `time.time()`, `time.perf_counter()`, `time.monotonic()`, `datetime.now()`
  anywhere in the execution path.
- `asyncio.get_event_loop().time()` inside an executor or plant step.
- `await asyncio.sleep(N)` to "throttle" or "pace" the simulation. The
  simulation's pace is determined by `SimClock.advance(scan_period_ms)`,
  not by real time.
- A plant model that measures its own `step()` duration with `perf_counter`
  to "catch up" to real-time.
- An ST primitive like `CURRENT_TIME()` that reads wall clock.
- A comm strategy that uses real timestamps for message ordering.
- Logging that injects `datetime.now()` into a value that lands in the
  trace (would make the trace non-reproducible).

## What is NOT covered by this invariant

- **The harness itself.** The harness may need wall clock for things like
  "advance simulated time at 1× real-time for live-display mode" or
  "timeout the whole simulation after N real seconds." Those are harness
  concerns; they don't enter the execution path.
- **Test code.** Tests may use `time` for measurement of their own runtime,
  for example to assert that a 100-scan simulation completes in under
  some threshold. The asserted property is wall-clock; that's fine.
- **Build, tooling, CI infrastructure.** Anything outside the execution
  path is unconstrained.
- **Logging metadata in the harness.** Per-run timestamps in log output
  are fine; they just must not flow into `TraceLog` or any value the
  verifier reads.

## Failure mode this prevents

A contributor adds `await asyncio.sleep(0.0001)` inside the scan loop "to
keep the event loop healthy under load." Tests pass — assertions still
fire within their tolerances. Replay works on their machine.

A teammate runs the same spec on a slower CI box. The sleep slides into a
different scheduling pattern. Now the trace records `belt_b_enable` at scan
14 instead of scan 13 — still well within the 500 ms tolerance, so the test
passes. But two traces from the same spec are no longer identical. Anyone
trying to use those traces as a baseline for regression testing finds
"changes" that aren't really changes.

A few months later someone trying to understand a failure runs the spec
five times and gets five different traces. The failing scan changes
position run-to-run. The classifier hypothesis "comm buffer promoted
stale data at scan 47" is now meaningless because there is no
deterministic scan 47.

The framework's central claim — *given this spec and this ST, here is
exactly what happens* — silently became *given this spec and this ST,
here is approximately what happens, mostly*.

## Examples in this codebase

- **`SimClock`** ([relay/clock.py](../../relay/clock.py))
  — frozen, holds `tick` and `elapsed_ms`, advances via explicit method.
- **`PLCCoroutine.run`** ([relay/runtime/plc.py:54](../../relay/runtime/plc.py#L54))
  — receives clock from a queue, never reads wall time.
- **`STContext`** ([relay/st/interpreter.py](../../relay/st/interpreter.py))
  — holds variables, timers, and the per-scan `assigned` set. The context
  carries no elapsed-time field of its own; per-scan time always arrives
  as the `dt_ms` argument to `execute(...)`. The context never queries
  the clock.
- **`ConveyorPlant.step`** ([relay/plant/conveyor.py:50](../../relay/plant/conveyor.py#L50))
  — takes `elapsed_ms` as a parameter, advances physics by that amount.
- **The conveyor test harness** ([tests/test_conveyor.py:114](../../tests/test_conveyor.py#L114))
  — calls `clock.advance(SCAN_PERIOD_MS)` to step time forward; never
  reads wall time inside the loop.

## Enforcement (suggested mechanical check)

A test or pre-commit hook that walks `relay/runtime/`, `relay/plant/`,
`relay/st/`, and any registered comm strategy module, parses imports,
and fails on `time`, `datetime`, or `asyncio.sleep` (with `await`)
appearing in execution-path code. Allowlist the harness file explicitly
once it lands.

## Related

- CLAUDE.md `## Don't` — "Read wall clock in any PLC executor — clock is
  always injected via `SimClock`" (the local form of this invariant)
- CLAUDE.md `Clock-Injection` capability — "This is what makes simulation
  deterministic and replay-able"
- [scan_phase_isolation.md](scan_phase_isolation.md) — both invariants
  together guarantee that a scan is a pure function of its inputs
- Glossary: "SimClock — External tick counter and elapsed_ms; injected,
  never read from wall clock"
