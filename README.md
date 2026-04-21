# relay

**Intent-driven PLC simulation.** Describe what a factory cell should *do* in natural language; relay generates the control logic, simulates it against a plant model, and verifies the behavior against trace-based assertions.

Relay is for engineers who want to prototype and test [PLC](#glossary) control strategies without a physical rig — and without trusting an LLM to tell them whether the result is correct.

---

## What it does

Relay takes a pipeline through four stages:

1. **Intent** — natural-language description of a control task ("when a part reaches the end of belt A, hand it off to belt B").
2. **Task spec** — a YAML intermediate representation. This is the project's [semantic IR](#glossary): everything downstream reads from it.
3. **Structured Text** — the task spec is compiled into [IEC 61131-3 Structured Text](#glossary) function blocks, the same language real PLCs run.
4. **Simulation + verification** — the generated code runs against a plant physics model, and the resulting scan-by-scan trace is checked against temporal assertions (`EVENTUALLY`, `PRECEDES`) written in Python.

The LLM sits on the front half of the pipeline (stages 1–3). **It is deliberately kept out of the verification path.** Assertions are plain Python evaluated against a deterministic trace log — so when a test passes, it passes for reasons you can inspect.

## Who it's for

- Control systems engineers prototyping multi-PLC handoff logic.
- Simulation engineers who want deterministic, replay-able runs.
- Researchers exploring LLM-driven code generation where the *verification* loop is non-negotiable.

If you're looking for a full IEC 61131-3 runtime, a SCADA system, or a hardware-in-the-loop rig, this isn't that. Relay implements a strict subset of Structured Text — only what its generator emits — and simulates plant physics at the minimum fidelity needed to exercise the control logic.

## How it works

Each simulated PLC is an `asyncio` coroutine executing a conventional scan loop:

```
promote comm buffer → snapshot I/O → execute function block → write outputs → publish
```

A few disciplines are load-bearing:

- **External clock.** No PLC reads the wall clock. `SimClock` is injected into every scan, which is what makes a run deterministic and replay-able.
- **Immutable I/O image.** The snapshot taken at the top of each scan is frozen for the duration of execution — inputs can't shift under you mid-scan.
- **No shared state between PLCs.** All coordination flows through a simulated Modbus TCP comm bus with per-scan message promotion. If a handoff works in the trace, it works because the messages actually moved.
- **Trace-based verification.** Every scan's I/O snapshot and outputs are recorded. Assertions are evaluated against that log, not against a live system.

## Example: conveyor handoff

The one worked example so far — a two-PLC conveyor with a part handoff between belts.

**Intent (informal):** When PLC A's exit sensor sees a part, signal PLC B; PLC B enables its belt; the part should arrive at B within 500ms, and the handoff signal must precede belt B enabling.

**Task spec** ([`specs/conveyor_handoff.yaml`](specs/conveyor_handoff.yaml)):

```yaml
System:
  plcs:
    - { id: plc_a, role: upstream }
    - { id: plc_b, role: downstream }
  comm: modbus_tcp

Plant:
  belt_speed: 0.5m/s
  sensor_trigger_threshold: 0.1m
  actuator_latency: 50ms

Behavior:
  plc_a:
    owns: [belt_a, sensor_a_exit]
    on: part_detected_at_exit -> signal_handoff
  plc_b:
    owns: [belt_b, sensor_b_entry]
    on: handoff_signal -> enable_belt_b

Assertions:
  - EVENTUALLY(part_at_b, within: 500ms)
  - PRECEDES(handoff_signal, belt_b_enable)
```

**Verification** (excerpt from [`tests/test_conveyor.py`](tests/test_conveyor.py)):

```python
trace = _run_simulation()
result = evaluate_assertion("EVENTUALLY(part_at_b, within: 500ms)", trace)
assert result.passed, result.reason
```

The test also verifies the negative case (if PLC A never signals, the part never arrives) and two framework invariants (I/O image stays immutable during a scan; the clock is externally driven).

## Getting started

Requires Python 3.11+.

```bash
# install (uv recommended)
uv sync

# run the conveyor demo tests
uv run pytest tests/test_conveyor.py -v
```

You'll need an `ANTHROPIC_API_KEY` in your environment to exercise the generator passes (stages 1 and 3 above). The simulation and verification stages don't call out to any LLM.

## Repository layout

```
relay/
├── runtime/       PLC coroutine, scan loop, I/O image, comm bus, sim clock
├── plant/         Plant physics models (currently: conveyor)
├── st/            Structured Text subset interpreter + function blocks
├── spec/          Task spec loader
├── generator/     LLM passes — NL → task spec, task spec → ST
└── verify/        Trace log + assertion evaluator (EVENTUALLY, PRECEDES)
specs/             Task spec YAML examples
tests/             End-to-end scenario tests
docs/invariants/   Subsystem invariants
```

See [`CLAUDE.md`](CLAUDE.md) for the engineering conventions this project is built under (immutability, clock injection, LLM-out-of-verification, etc.).

## Status

Early. The conveyor handoff is the first working end-to-end scenario. The task spec JSON Schema is intentionally deferred until a second demo derives it from practice rather than speculation. The ST interpreter covers only what the generator currently emits (variables, `IF/THEN`, `TON` timers, arithmetic) and is meant to grow alongside the generator, not ahead of it.

## Glossary

- **PLC** — Programmable Logic Controller. A ruggedized industrial computer that runs a fixed control program in a tight scan loop (typically every few milliseconds).
- **IEC 61131-3 Structured Text (ST)** — A standardized, Pascal-like programming language for PLCs.
- **Semantic IR** — Intermediate representation that captures the *meaning* of the input (here, the task spec YAML) and is what all downstream stages read from.
- **Function block** — A reusable unit of ST code that executes each scan and carries internal state (e.g., timer accumulators) between scans.
- **Scan** — One cycle of a PLC's main loop. Inputs are sampled, logic runs, outputs are written.

---

*This README assumes a general technical audience — comfortable reading code, but not necessarily familiar with PLC programming. Let me know if you'd like a version pitched at stakeholders, executives, or a non-technical reader.*
