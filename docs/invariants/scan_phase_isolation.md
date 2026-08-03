# Invariant: Scan phases are canonical and ST cannot escape its phase

**Status:** Active | **As-Of:** 2026-04-21 | **Scope:** `relay/runtime/`, `relay/st/`, comm strategies, plant models

## Statement

Each PLC scan executes in a fixed phase order:

1. **Consume clock** — receive `SimClock` from the harness
2. **Drain bus** — collect pending inter-PLC messages into a `CommBuffer`
3. **Promote** — fold `CommBuffer` contents into the IOImage
4. **Snapshot** — capture the IOImage immediately before execution
5. **Execute** — run the function block against the snapshot
6. **Emit outgoing** — return `(target, key, value)` tuples for the bus
7. **Fold outputs** — apply executor outputs to the IOImage for the next scan
8. **Record** — append a `ScanRecord` to the `TraceLog`

The phase order is fixed and one-way. Function block execution (phase 5) is
a pure function of the snapshot, the comm buffer, the clock, and the prior
`STContext`. ST may not perform I/O of any kind during execution: no fresh
sensor reads, no `bus.send`, no mutation of the snapshot or comm buffer, no
re-entry into earlier phases.

## Why

The whole abstraction of a PLC scan cycle depends on phase isolation.
Real PLCs sample inputs at scan top, run logic against that sample, write
outputs at scan bottom. Coordination bugs — sensor-debounce errors,
actuator-latency races, comm-buffer staleness — are *defined by* the gap
between when a value was sampled and when it was acted on.

If ST can read a fresh sensor value mid-scan, that gap collapses for some
signals and not others. The simulator silently models a faster, more
responsive PLC than any real hardware would be. The bugs the framework
exists to catch are exactly the ones it stops being able to catch.

The canonical order is also what makes the trace meaningful. `ScanRecord.io`
is the *snapshot* (phase 4), not whatever the IOImage drifted to during
execution. PRECEDES and EVENTUALLY assertions reason about scan-bottom
output values, not mid-scan transients. Allowing ST to emit values outside
the canonical channel breaks that contract.

## What this looks like

1. **`PLCCoroutine.run` is the canonical loop.** Other harness code may
   compose around it (drive multiple PLCs, advance the clock) but may not
   reorder its internal phases.
2. **`FBExecutor` is a pure function.** Signature:
   `(IOImage, CommBuffer, SimClock, dt_ms) -> (IOImage, list[outgoing])`.
   Same inputs produce same outputs. No I/O, no clock reads, no mutation
   of arguments.
3. **ST interpreter has no I/O primitives.** The only effects of executing
   a block are updates to `STContext` (variables, timers, `assigned` set).
   No primitive reaches outside the context.
4. **Outputs travel by exactly one channel: assignment.** A PLC's outputs
   for a scan are exactly the names in `STContext.assigned`. There is no
   "emit" or "publish" primitive that bypasses this.
5. **The snapshot is captured before execution and never re-read inside.**
   Execution sees `snapshot`; subsequent fold-outputs writes to `io` (the
   live IOImage) for the next scan, not for the current one.

## What violates this invariant

- An ST primitive that reads a sensor value not present in the snapshot
  (e.g. `READ_INPUT('sensor_a_exit')`).
- An ST primitive that calls `bus.send` mid-execution.
- A `FUNCTION_CALL` in the interpreter that invokes Python code with side
  effects.
- A scan-loop "optimization" that reuses the prior scan's snapshot
  instead of taking a fresh one (skips phases 2–4).
- A "fast" path that lets the executor write directly to the next scan's
  IOImage, bypassing the fold-outputs phase that records what the PLC
  actually produced.
- A comm strategy that pushes received messages into the IOImage *during*
  phase 5 instead of at promote-time.
- Recording the trace from inside the executor instead of at phase 8 —
  recorded outputs would not match the published outputs.

## What is NOT covered by this invariant

- **The internal ordering of statements within a single ST block.** ST
  semantics — sequential evaluation of IF/assignment/TON — are governed by
  the IEC 61131-3 subset the interpreter implements. That's separate.
- **Plant physics ordering.** The plant takes one `step()` per scan; how
  it orders sub-computations inside that step is plant-internal.
- **Trace post-processing.** Code that reads the completed trace can
  iterate it however it wants. The invariant constrains the live scan loop,
  not analysis of finished traces.
- **Multi-PLC ordering.** Different PLCs may execute their scan phases in
  parallel; the invariant is per-PLC. Cross-PLC ordering is governed by
  CommBus semantics (see `comm_bus_only_inter_plc_channel.md`).

## Failure mode this prevents

A contributor wants ST to be able to query "the latest value of sensor X"
without waiting for the next scan. They add `READ_FRESH('sensor_x')` to the
interpreter, which bypasses the snapshot and reads from the live IOImage.

A few scenarios get noticeably more responsive — bugs that used to require
two scans to manifest now resolve in one. Tests pass. The contributor
ships it.

Six months later, generated ST starts using `READ_FRESH` opportunistically
because the LLM noticed the construct and likes its brevity. Now the
simulator is modeling PLCs that don't exist — ones with zero-latency
sensor reads. A whole class of debounce-related coordination bugs
becomes invisible. The framework's "we caught it in sim" guarantee is
silently false for any scenario the generator chose to use the new primitive.

## Examples in this codebase

- **`PLCCoroutine.run`** ([relay/runtime/plc.py](../../relay/runtime/plc.py))
  — the canonical 8-phase loop.
- **`FunctionBlock.scan`** ([relay/runtime/fb.py](../../relay/runtime/fb.py))
  — reads inputs into context, executes, harvests `assigned` as outputs.
  No I/O, no bus access.
- **`STContext.assign`** ([relay/st/interpreter.py](../../relay/st/interpreter.py))
  — the only path by which a name becomes an output. The interpreter has
  no other effect surface.

## Enforcement (suggested mechanical check)

- A test that drives two **fresh** `FunctionBlock` instances through the same
  input *sequence* and asserts the two output *sequences* are identical.
  Same history → same outputs proves no I/O or nondeterminism is being read.

  Compare sequences, not single scans, and construct a new `FunctionBlock`
  per run. `FunctionBlock` owns `_ctx` as instance state, and phase 5 is pure
  *given the prior `STContext`* — not stateless. Driving one instance twice
  with identical inputs and expecting identical outputs tests statelessness,
  which this invariant never claimed and which correct ST does not have: a
  latch (`IF x AND NOT latched THEN latched := TRUE`) emits on its first
  scan and goes quiet on the second, by design. That check fails against
  correct code.
- A grep over `relay/st/` and `relay/runtime/` for forbidden imports
  (`asyncio.sleep`, `time.*`, `socket`, `requests`).

## Related

- CLAUDE.md `## Don't` — "Extend the ST interpreter beyond what the generator
  actually emits" (related but separate concern: this invariant constrains
  the *semantics* of any extension, that one constrains the *scope*)
- [comm_bus_only_inter_plc_channel.md](comm_bus_only_inter_plc_channel.md)
  — CommBus phase semantics depend on this invariant for the "promote at
  scan top" guarantee
- Glossary: "Scan — One execution cycle: promote comm → snapshot I/O →
  execute FB → write outputs → publish"
