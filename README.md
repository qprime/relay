# relay

**Compile natural-language control intent into deterministic, verified PLC simulations.**

Relay takes a description of what a factory cell should do and runs it through a four-stage pipeline: intent → task spec → IEC 61131-3 Structured Text → scan-cycle simulation → trace-based verification. The LLM generates the code; verification is plain Python against a deterministic trace log — so when a test passes, it passes for reasons you can inspect.

## What this is

Relay is a compiler-shaped framework for prototyping PLC control strategies without physical hardware — and without trusting an LLM to tell you whether the result is correct. The pipeline has four stages:

1. **Intent** — a natural-language description of a control task ("when a part reaches the end of belt A, hand it off to belt B").
2. **Task spec** — a YAML intermediate representation that captures the semantic meaning of the intent. Everything downstream reads from this.
3. **Structured Text generation** — the task spec compiles into IEC 61131-3 Structured Text function blocks, the same language real PLCs run.
4. **Simulation and verification** — the generated code executes against a plant physics model, and the resulting scan-by-scan trace is checked against temporal assertions (`EVENTUALLY`, `PRECEDES`) written in Python.

The LLM sits on the front half of the pipeline (stages 1–3). It is deliberately excluded from the verification path — assertions are evaluated against a deterministic trace log, not by asking an LLM whether the output looks right.

Each simulated PLC runs as an `asyncio` coroutine executing a conventional scan loop:

```
promote comm buffer → snapshot I/O → execute function block → write outputs → publish
```

### Conveyor handoff example

The project's first end-to-end scenario: a two-PLC conveyor with a part handoff between belts.

**Intent:** When PLC A's exit sensor sees a part, signal PLC B; PLC B enables its belt; the part should arrive at B within 500 ms, and the handoff signal must precede belt B enabling.

**Task spec** ([`specs/conveyor_handoff.yaml`](specs/conveyor_handoff.yaml)):

```yaml
System:
  name: conveyor_handoff
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

**Verification** (from [`tests/test_conveyor.py`](tests/test_conveyor.py)):

```python
trace = _run_simulation()
result = evaluate_assertion("EVENTUALLY(part_at_b, within: 500ms)", trace)
assert result.passed, result.reason
```

The test also checks the negative case (PLC A never signals → part never arrives) and two framework invariants (I/O image immutability during a scan; externally driven clock).

## Core representation and framework discipline

**Task spec** is relay's semantic IR. Every stage downstream of intent parsing reads from the task spec YAML — it defines what PLCs exist, what they own, how they behave, and what assertions must hold. A change to the task spec schema reshapes the entire pipeline.

Four invariants make the simulation deterministic and the verification trustworthy:

| Invariant | Mechanism |
|-----------|-----------|
| External clock | `SimClock` is injected into every scan. No PLC reads the wall clock. |
| Immutable I/O image | The snapshot taken at scan-top is frozen for the duration of execution — inputs can't shift mid-scan. |
| No shared PLC state | All coordination flows through a simulated Modbus TCP comm bus with per-scan message promotion. |
| Trace-based verification | Every scan's I/O snapshot and outputs are recorded. Assertions evaluate against the log, not a live system. |

If a handoff works in the trace, it works because the messages actually moved through the comm bus at the right scan boundaries.

## How to use

### 1. Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### 2. Run the conveyor demo

```bash
uv run pytest tests/test_conveyor.py -v
```

This runs the two-PLC conveyor handoff end-to-end: generates Structured Text from the task spec, simulates the plant, and evaluates temporal assertions against the trace log.

### 3. Run all tests

```bash
uv run pytest
```

Generator passes (stages 1 and 3) require an `ANTHROPIC_API_KEY` in your environment. Simulation and verification stages do not call out to any LLM.

## Project structure

```
relay/
├── spec/          Task spec loader and validation
├── generator/     LLM passes — NL → task spec, task spec → ST
├── st/            Structured Text subset interpreter and function blocks
├── runtime/       PLC coroutine, scan loop, I/O image, comm bus, SimClock
├── plant/         Plant physics models (currently: conveyor)
└── verify/        Trace log and assertion evaluator (EVENTUALLY, PRECEDES)
specs/              Task spec YAML examples
tests/              End-to-end scenario tests
docs/invariants/    Subsystem invariants
```

The pipeline flows left-to-right through the subsystem list: `spec/` → `generator/` → `st/` → `runtime/` + `plant/` → `verify/`. The `runtime/`, `plant/`, and `verify/` subsystems form a coupled surface — a change to scan-cycle structure or I/O image layout in one must be checked against the other two.

## Scope boundaries

| In scope | Out of scope |
|----------|--------------|
| Subset of Structured Text the generator emits | Full IEC 61131-3 language coverage |
| Plant physics at minimum fidelity for exercising control logic | High-fidelity physics simulation or digital twin |
| Deterministic trace-based post-verification | LLM-in-the-loop verification or static analysis of generated ST |
| Multi-PLC coordination via simulated comm bus | Real Modbus TCP, fieldbus protocols, or hardware-in-the-loop |
| Prototyping and testing control strategies | Production PLC deployment or runtime |
| YAML task specs as the compiler's input language | General-purpose NL-to-PLC without a structured IR |
| ST interpreter grows alongside the generator | Ahead-of-generator language coverage |

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` for generator passes (simulation and verification are offline)
- See [`pyproject.toml`](pyproject.toml) for the full dependency list

## License

Copyright © 2026 Sean Quinlan. All rights reserved.

This repository is published for portfolio and review purposes. No license is granted to use, copy, modify, or distribute this code or its contents. If you're interested in using any part of this work, please get in touch.

## Glossary

- **PLC** — Programmable Logic Controller. A ruggedized industrial computer that runs a fixed control program in a tight scan loop (typically every few milliseconds).
- **IEC 61131-3 Structured Text (ST)** — a standardized, Pascal-like programming language for PLCs.
- **Task spec** — relay's semantic intermediate representation: a YAML document capturing what PLCs exist, what they own, how they behave, and what temporal assertions must hold.
- **Function block** — a reusable unit of ST code that executes each scan and carries internal state (e.g., timer accumulators) between scans.
- **Scan** — one cycle of a PLC's main loop. Inputs are sampled, logic runs, outputs are written.
- **SimClock** — relay's injected external clock. All scan-cycle timing flows through SimClock, making runs deterministic and replay-able.
