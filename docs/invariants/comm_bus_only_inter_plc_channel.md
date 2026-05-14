# Invariant: CommBus is the only inter-PLC channel

**Status:** Active | **As-Of:** 2026-04-21 | **Scope:** `relay/runtime/`, scenario tests

## Statement

All coordination between PLC coroutines flows through `CommBus`. No module
under `relay/runtime/` may hold mutable state keyed by `plc_id` that PLCs
read or write. No PLC executor, plant model, or comm strategy may reach across
to another PLC's IOImage, STContext, or output stream by any path other than
`CommBus.send` / `CommBus.drain`.

The bus's timing semantics — drain at scan top, promote pending into the
IOImage before execution — are part of the invariant. A "fast path" that
bypasses promotion violates this rule even if it routes through `CommBus`
nominally.

## Why

Modeling inter-PLC comm latency is one of the framework's load-bearing claims.
Real coordination bugs live at the seam between scan cycles: a signal
published one scan late, a buffer promoted with stale data, a race between two
PLCs both waiting for a third. The simulator only catches those bugs if every
inter-PLC signal pays the same scan-boundary cost the real network would impose.

Any side channel — a shared sensor cache, a "for performance" direct read,
a global registry of last-known-values — silently makes some signals
free of latency that real signals would pay. Tests that exercise the bypassed
path pass; tests that exercise the modeled path catch real bugs. The
asymmetry is invisible from any single test or any single file.

## What this looks like

1. **One `CommBus` instance per simulation.** Constructed in the harness;
   passed to every `PLCCoroutine`. Not a global; not a singleton.
2. **PLC coroutines exchange data only via `bus.send` / `bus.drain`.** The
   `outgoing` list returned from an executor is the PLC's complete output
   surface for the network.
3. **Promote-at-scan-top is the only way drained data enters the IOImage.**
   Direct `IOImage.with_value` in the scan body for non-plant inputs is a
   violation.
4. **Comm strategies are the only producers of inter-PLC routing decisions.**
   Plant-routed sensors flow plant → bus → PLC; PLC-routed signals flow
   PLC → strategy → bus → PLC. Nothing else.
5. **No module-level dict keyed by `plc_id`** in `relay/runtime/`,
   `relay/plant/`, or `relay/st/`. Per-PLC state lives inside that PLC's
   coroutine.

## What violates this invariant

- A shared `dict[str, Any]` accessible to multiple PLCs at runtime.
- A "subscribe" hook that lets one PLC observe another's output without going
  through `CommBus`.
- A test fixture that mutates a target PLC's IOImage to "deliver" a value —
  the value should arrive via `bus.send` from the harness instead.
- A comm strategy that synchronously calls into a target PLC's executor
  rather than enqueuing on the bus.
- A plant model that, given knowledge of the upstream PLC's outputs,
  short-circuits to deliver a downstream signal without round-tripping
  through the bus.

## What is NOT covered by this invariant

- **Single-PLC internal state.** A PLC's `STContext`, its own IOImage, its
  own scan-local timers — all fine, all expected. The rule is about *cross*-PLC
  paths.
- **Plant internal state.** `ConveyorPlant._state` is plant physics, not PLC
  state. Plants legitimately hold mutable simulation state.
- **The trace log.** `TraceLog` is a sink, not a coordination channel. PLCs
  do not read from the trace.

## Failure mode this prevents

A scenario adds a third PLC that "obviously" needs to know what PLC A's belt
is doing. A contributor adds `latest_outputs: dict[str, IOImage]` keyed by
plc_id, updated at the bottom of every scan, readable by any executor. Coordination
tests pass — the third PLC sees the right value.

Six months later, a generated control program races on `belt_a_running` and
the bug only manifests on real hardware where the signal pays a 12 ms
network round trip. Sim never modeled that latency for `belt_a_running`
because the cache returned it free. The framework's "we caught it in
simulation" guarantee silently false for an entire class of signals.

## Examples in this codebase

- **`PLCCoroutine.run`** ([relay/runtime/plc.py:24-52](../../relay/runtime/plc.py#L24-L52))
  — the only place inter-PLC data enters a coroutine is `await bus.drain(...)`
  followed by `comm.promote()` into the IOImage at the top of each scan.
- **Simulation harness** ([relay/runtime/harness.py](../../relay/runtime/harness.py))
  — the per-scan loop (`harness.py:94-115`) routes plant sensors to PLCs via
  `bus.send(target, key, value)` after `plant.route_to_plcs(...)`, and routes
  inter-PLC tags via `bus.send(...)` after the comm strategy's `route(...)`
  call. No path writes another PLC's IOImage directly. The end-to-end
  conveyor test ([tests/test_conveyor.py](../../tests/test_conveyor.py))
  exercises this harness — it calls `simulate(spec, blocks, ...)` rather than
  wiring PLCs itself, so all inter-PLC routing decisions flow through the
  harness's `bus.send` calls.
- **Comm strategy registry** ([relay/strategies/comm.py](../../relay/strategies/comm.py))
  — `build_comm_strategy(name, comm_block)` resolves the strategy named in
  the spec's `Comm.strategy` field; the registry raises on unknown values.
  The registry lives in `relay/strategies/` rather than `relay/runtime/` so
  that `relay/spec/` can import it for spec-time validation without violating
  [pipeline_direction_imports.md](pipeline_direction_imports.md). Today the
  registry contains `tag` (live; used by the conveyor demo) and `address`
  (a stub that raises `NotImplementedError`, reserved for a future
  Modbus TCP-style implementation).

## Related

- CLAUDE.md `## Don't` — "Share state between PLC coroutines" (the local
  form of this invariant)
- [pluggable_subsystems.md](pluggable_subsystems.md) — comm strategies are
  pluggable; this invariant constrains *all* strategies, not just the default
- Glossary: "Comm buffer — Pending inter-PLC messages promoted each scan;
  simulates Modbus TCP latency"
