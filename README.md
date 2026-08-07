# relay

**Compile natural-language control intent into deterministic, verified PLC simulations — then carry the verdict onto a real deployment target.**

Relay takes a description of what a factory cell should do and runs it through a four-stage pipeline: intent → task spec → IEC 61131-3 Structured Text → scan-cycle simulation → trace-based verification. The task spec is a hand-authorable semantic IR; verification is plain Python against a deterministic trace log — so when a test passes, it passes for reasons you can inspect. Spec authoring is conversational and happens outside the repo; the entry point here is a validated YAML file, and everything downstream of it is a deterministic compiler.

The same verdict is then re-earned on a C++23 deployment host running wall-clock-paced, free-running scan cycles — the same spec, the same assertions, a harder environment.

It is a spec-first framework for prototyping and verifying distributed control behavior in deterministic simulation — not a production PLC toolchain and not a safety-certification system.

## What this is

Relay is a compiler-shaped framework for prototyping PLC control strategies without physical hardware — and without trusting an LLM to tell you whether the result is correct. The pipeline has four stages:

1. **Intent** — a natural-language description of a control task ("when a part reaches the end of belt A, hand it off to belt B"). This stage is a conversation with an agent, not a step the repo runs; the repo's job starts at the artifact it produces.
2. **Task spec** — a YAML intermediate representation that captures the semantic meaning of the intent. Everything downstream reads from this. `python -m tools.validate_spec <spec>` is the gate: it runs the full validator and prints every issue at once, so a spec is checked before anything is generated from it.
3. **Structured Text generation** — the task spec's `Behavior` block is a structured trigger IR (edge/level detection, debounce, latch/pulse/steady emission), which a deterministic Python compiler translates into IEC 61131-3 Structured Text function blocks, the same language real PLCs run. Every timing and edge semantic is written in the spec and inspectable there, not chosen downstream.
4. **Simulation and verification** — the generated code executes against a plant physics model, and the resulting scan-by-scan trace is checked against temporal assertions (`EVENTUALLY`, `PRECEDES`, `CAUSES`) written in Python.

The LLM is external to the pipeline and strictly upstream of the IR: it helps author the task spec, and once that spec exists, every downstream artifact — ST, simulation trace, verdict — is deterministically derived from it. No relay module calls a model. The LLM is deliberately excluded from the verification path — assertions are evaluated against a deterministic trace log, not by asking an LLM whether the output looks right.

Each simulated PLC runs as an `asyncio` coroutine executing a conventional scan loop:

```
promote comm buffer → snapshot I/O → execute function block → write outputs → publish
```

Coordination between PLCs is modeled by a pluggable **comm strategy** — a registered implementation of how inter-PLC signals get routed and when they become visible at the receiver. The conveyor demo uses the `tag` strategy: each tag is declared in the task spec (producer + consumers), and the runtime promotes pending tag values into the consumer's I/O image at the top of the next scan, paying a one-scan latency cost that mirrors a real network. Precisely: a message becomes visible at the consumer's first scan top whose `SimClock` time is **strictly later** than the sending scan's, so delivery is paced by the consumer's sampling and costs up to one *consumer* scan period. Plant routes are exempt — a sensor wired to the input terminals is sampled at scan top, not delivered over a network. An `address` strategy (intended to model Modbus TCP register maps) is registered as a stub today and raises `NotImplementedError`; address-based comm is aspirational, not implemented. The framework's identity as a multi-protocol simulator is intentional, but only the tag strategy is live.

The C++ host does not charge this cost: its clock referent is the wall clock, and its in-process channel models a backplane with near-zero latency, so it pays between zero and roughly one period. The asymmetry is deliberate — the sim is the **conservative** oracle, so a budget derived from its measurement covers a host that is at worst as slow. Verdict equality is per-assertion pass/fail and is unaffected by the difference in measured gaps.

### Where this sits

Every individual piece of this exists commercially, and most of it exists in the open. Siemens Engineering Copilot generates SCL from natural language inside TIA Portal. S7-PLCSIM Advanced and Rockwell FactoryTalk Logix Echo emulate real controllers — Echo with controllable virtual time, the nearest commercial relative of `SimClock`. Factory I/O, SIMIT, and Emulate3D model plants at far higher fidelity than `relay/plant/` attempts. Beremiz, MATIEC, and OpenPLC compile and run IEC languages. CERN's PLCverif model-checks SCL against temporal properties, which is a stronger claim than trace testing makes.

What I did not find was a vendor-neutral tool chaining all of it: intent → a semantic IR you can read and edit by hand → generated code → deterministic multi-PLC simulation → an inspectable verdict, with an evidence chain carrying that verdict outward to less-controlled environments. The commercial assembled stack (Copilot → TIA/SCL → PLCSIM Advanced → SIMIT) covers the same ground with vastly more fidelity, but it is an ecosystem rather than a single inspectable compiler, and it is Siemens all the way down.

So the interesting part here is not the code generation and not the simulator. It is the seam between them: a reviewable IR, plain-Python temporal assertions, and a verification path with a closed import set that no model output can reach. Those tools win on fidelity, vendor compatibility, 3D modeling, and hardware integration; formal tools win on exhaustive proof. Relay is the fast vendor-neutral layer upstream of both — where you find out whether the control strategy is right before committing to any of them.

Exports and adapters into OpenPLC, CODESYS, Factory I/O, or PLCverif are the obvious direction and none exist today.

### Conveyor handoff example

The project's first end-to-end scenario: a two-PLC conveyor with a part handoff between belts.

**Intent:** When PLC A's exit sensor sees a part, signal PLC B; PLC B enables its belt; the part should arrive at B within 400 ms, the handoff signal must precede belt B enabling, and belt B must enable *because of* the handoff message rather than coincidentally.

**Task spec** ([`specs/conveyor_handoff.yaml`](specs/conveyor_handoff.yaml)):

```yaml
System:
  name: conveyor_handoff
  plcs:
    - { id: plc_a, role: upstream }
    - { id: plc_b, role: downstream }

Comm:
  strategy: tag
  tags:
    - { name: handoff_signal, produced_by: plc_a, consumed_by: [plc_b] }

Plant:
  type: conveyor
  config:
    belt_speed_m_per_s: 0.5
    sensor_trigger_threshold_m: 0.1
    actuator_latency_ms: 50.0
  routes:
    - { sensor: sensor_a_exit_triggered, to_plc: plc_a, as_key: sensor_a_exit, trigger: edge }
    - { sensor: part_at_b,               to_plc: plc_b, as_key: part_at_b,     trigger: level }
  actuators:
    - { from_plc: plc_b, key: belt_b_enable, as: belt_b_enable_signal }

Behavior:
  plc_a:
    triggers:
      - id: handoff_on_exit
        when: { signal: sensor_a_exit, edge: rising }
        emit: { tag: handoff_signal, mode: latched }
  plc_b:
    triggers:
      - id: belt_on_handoff
        when: { signal: handoff_signal, edge: level }
        emit: { output: belt_b_enable, mode: latched }

Assertions:
  - "EVENTUALLY(part_at_b, within: 400ms)"
  - "PRECEDES(handoff_signal, belt_b_enable, within: 50ms)"
  - "CAUSES(handoff_signal, belt_b_enable)"
```

The `Comm` block selects a comm strategy (currently `tag`; see below) and declares the inter-PLC signals it routes. The `Plant` block selects a plant model (`conveyor` is the only one the Python registry holds today) and wires named plant sensors to PLC input keys and PLC output keys to plant actuators. Both `Comm.strategy` and `Plant.type` are registry lookups, so adding a new variant is additive — no framework branching. The C++ host keeps its own plant registry, which adds `remote_socket` for a plant in another process; that is a host-side selection, not a `Plant.type` a task spec can declare (see [host/README.md](host/README.md)).

Full field-by-field syntax, including the rules the validator enforces and the ones it can't, is in [docs/task_spec_syntax.md](docs/task_spec_syntax.md); the tables below are the summary.

The `Behavior` block is the trigger IR the ST compiler reads. Each PLC declares a list of triggers, and each trigger compiles to one ST stanza:

| Field | Values | Meaning |
|-------|--------|---------|
| `when.signal` | string | A `Plant.routes[].as_key` targeting this PLC, or a `Comm.tags[]` entry it consumes |
| `when.edge` | `rising` \| `falling` \| `level` | Transition to detect; `level` fires while the signal is true |
| `when.debounce_ms` | int ≥ 0 | Source must hold stable this long before the trigger fires — which shifts the edge, not just the timing ([details](docs/task_spec_syntax.md#debounce_ms-shifts-the-edge-not-only-the-timing)) |
| `emit.tag` \| `emit.output` | string (exactly one) | A tag this PLC produces, or a local output name |
| `emit.mode` | `latched` \| `pulse` \| `steady` | `latched` sets once and holds; `steady` follows the condition down; `pulse` asserts for `duration_ms` |
| `emit.duration_ms` | int > 0 | Required when `mode: pulse`, rejected otherwise |

Because edge, debounce, and pulse width are spec fields rather than downstream inferences, an under-specified scenario fails at validation instead of quietly simulating the wrong behavior. Validation resolves every `when.signal` and `emit.tag` against the `Plant` and `Comm` blocks, so a trigger cannot reference a signal no PLC can read or emit a tag it does not produce.

Each compiled stanza carries a provenance marker naming the trigger it came from, so generated ST reads back against the spec that produced it:

```
(* trigger: handoff_on_exit *)
_scratch_edge_handoff_on_exit := sensor_a_exit AND NOT _scratch_prev_handoff_on_exit;
_scratch_prev_handoff_on_exit := sensor_a_exit;
IF _scratch_edge_handoff_on_exit THEN
_scratch_latched_handoff_on_exit := TRUE;
END_IF;
_send_plc_b_handoff_signal := _scratch_latched_handoff_on_exit;
```

`_scratch_*` variables are compiler bookkeeping for edge and latch state. They are suppressed from the output image, so they never reach the trace or the verifier.

**Verification** (from [`tests/test_conveyor.py`](tests/test_conveyor.py)):

```python
trace = _run_simulation()
result = evaluate_assertion("EVENTUALLY(part_at_b, within: 400ms)", trace)
assert result.passed, result.reason
```

The test also checks the negative case (PLC A never signals → part never arrives) and two framework invariants (I/O image immutability during a scan; externally driven clock).

### Assertion forms

| Form | Asserts | Budget |
|------|---------|--------|
| `EVENTUALLY(signal, within: Nms)` | The signal becomes true within N ms of simulation start | Required |
| `PRECEDES(a, b, within: Nms)` | `a` becomes true no later than `b`, and the gap fits the budget | Required |
| `CAUSES(a, b)` | `b`'s first activation is attributable to a received message carrying `a` | None — reads no clock |

`PRECEDES` ordering is non-strict: same-scan is a pass, because within one scan there is no observable ordering. The budget is what survives independent clocks — two physical PLCs share no scan boundary, so "same scan" has no referent on hardware while a bounded gap does. The observed gap is reported on every evaluation, pass or fail, so budgets can be measured rather than guessed.

`CAUSES` answers a question timing cannot: *did this actually happen because of that message?* Each delivered message records a **receipt** at the point of delivery — the sender, that sender's per-key sequence number, and the value delivered. Attribution reads all three from the receipt rather than re-deriving them from the trace's merged signal view, which can say neither who sent a message nor what it carried. Because it reads no clock on the pass/fail path, the form survives the move off lockstep simulation onto free-running hardware. Only declared `Comm.tags` can be a cause; plant-routed signals record no sender and are unattributable by construction.

## Core representation and framework discipline

**Task spec** is relay's semantic IR. Every stage downstream of the task spec reads from the task spec YAML — it defines what PLCs exist, what they own, how they behave, and what assertions must hold. A change to the task spec schema reshapes the entire pipeline.

Four invariants make the simulation deterministic and the verification trustworthy:

| Invariant | Mechanism |
|-----------|-----------|
| External clock | `SimClock` is injected into every scan. No PLC reads the wall clock. |
| Immutable I/O image | The snapshot taken at scan-top is frozen for the duration of execution — inputs can't shift mid-scan. |
| No shared PLC state | All coordination flows through `CommBus` via a pluggable comm strategy with per-scan message promotion. The current strategy (`tag`) routes named signals from producer to consumer; address-based routing (Modbus TCP-style) is planned. |
| Trace-based verification | Every scan's I/O snapshot, outputs, sends, and receipts are recorded. Assertions evaluate against the log, not a live system. |

If a handoff works in the trace, it works because the messages actually moved through the comm bus at the right scan boundaries.

## Validation chain

The four-stage pipeline above (intent → spec → ST → sim → verify) is the **inner pipeline**. It produces a verdict on the spec in a single, deterministic environment. The **outer pipeline** carries that verdict outward through progressively less-controlled environments — each one introducing one new class of complexity the previous environment could not certify.

The principle: each stage is the contract for the next. The Python sim plays the role of **oracle** — it runs the spec under injected clock and in-process plant, then writes the assertions it certified into an expectations artifact at `specs/expectations/<system_name>.expected.json`. Every downstream stage runs the same spec in its own environment, emits its own trace, and is judged by the same verifier (`relay/verify/`) against that same artifact. The contract does not change as you walk the chain; only the evidence does.

This is what makes failures cheap to localize. When stage N satisfies the expectations and stage N+1 does not, the bug lives in whatever N+1 newly introduced — wall-clock pacing, an inter-process boundary, physical I/O. You never debug two new variables at once.

```
spec ──> Python sim ──> C++ host             ──> C++ host         ──> C++ host
         (oracle)       (in-process,             (Python plant         (real
                         stub plant)              over socket)          fieldbus)

           ↓                ↓                       ↓                     ↓
       expectations    same assertions         same assertions       same assertions
       artifact        re-evaluated            re-evaluated          re-evaluated
                       against host trace      against host trace    against host trace
```

| Stage | New complexity it adds | What it certifies | Status |
|-------|------------------------|-------------------|--------|
| Python sim (oracle) | Deterministic in-process execution under `SimClock` | The spec is realizable: generated ST plus plant model produce a trace where the assertions hold | ✅ |
| C++ host, in-process stub plant | Free-running wall-clock-paced scan cycles; C++ ST interpreter and scan executor | The generated ST and scan semantics survive a real runtime without violating the assertions | ✅ [#4](https://github.com/qprime/relay/issues/4), [#14](https://github.com/qprime/relay/issues/14) |
| C++ host, Python plant over socket | Inter-process boundary; network framing and latency | The runtime composes correctly with an out-of-process plant | ✅ [#14](https://github.com/qprime/relay/issues/14) |
| C++ host, real fieldbus | Physical I/O and real hardware timing | The control strategy works against the actual physical system | 🚧 [#17](https://github.com/qprime/relay/issues/17) |

Three of the four stages are live. The verdict earned in the Python oracle is re-earned by the C++ host, both against its in-process stub plant and against a Python plant reached over TCP.

### What survived the move off lockstep

The host does not run a scan barrier. Each PLC coroutine paces itself on its own timer at `scan_period_ms` and produces its own `SimClock` — `tick` increments per scan, `elapsed_ms` accumulates by that PLC's scan period, never read from the wall clock. The wall clock decides *when* a scan runs, never *what time it believes it is*.

The cost is that record interleaving and message-arrival scans are genuinely nondeterministic run to run. What replaces byte-for-byte reproducibility is a weaker but more meaningful contract: **verdict determinism** — the same certified verdicts on every run, per assertion, gated by ten consecutive passes in `tests/test_host_satisfies_expectations.py`. A host that *passes* an assertion Python failed is as wrong as the reverse.

This is where `CAUSES` earns its design. It is timing-free by construction, so interleaving skew cannot move it. The clock-dependent forms hold with measured headroom rather than luck — see [host/README.md](host/README.md) for the numbers.

## How to use

### 1. Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### 2. Validate the task spec

```bash
uv run python -m tools.validate_spec specs/conveyor_handoff.yaml
```

Checks a spec against the full validator — trigger IR, assertion grammar, per-strategy config, assertion coverage — and prints every issue in one run, exiting non-zero if any. Validate before you trust anything generated from a spec; the loader's own checks are narrower and a malformed `Behavior` block will otherwise surface as a confusing failure inside the compiler.

### 3. Run the conveyor demo

```bash
uv run pytest tests/test_conveyor.py -v
```

This runs the two-PLC conveyor handoff end-to-end: generates Structured Text from the task spec, simulates the plant, and evaluates temporal assertions against the trace log.

### 4. Run all tests

```bash
uv run pytest
```

The whole pipeline runs offline with no API key. ST compilation, simulation, and verification do not call out to any LLM, and no relay module depends on a model client.

### 5. Run the C++ host

Requires GCC 13+ or clang 17+ and CMake ≥ 3.22. The first configure fetches asio and googletest over the network.

```bash
cmake -S host -B host/build -DCMAKE_BUILD_TYPE=Release
cmake --build host/build -j
```

The host reads JSON, never YAML — Python emits its inputs at the language boundary:

```bash
python -m tools.emit_host_inputs specs/conveyor_handoff.yaml --out-dir /tmp/host_inputs
host/build/relay_host_main \
    --spec /tmp/host_inputs/resolved_spec.json \
    --st-blocks /tmp/host_inputs/st_blocks.json \
    --out /tmp/host_inputs/cpp_trace.jsonl
```

To run the host against a Python plant in a separate process:

```bash
python -m tools.plant_server specs/conveyor_handoff.yaml --port 0   # prints READY <port>
host/build/relay_host_main --spec ... --st-blocks ... --out ... --plant-endpoint 127.0.0.1:<port>
```

See [host/README.md](host/README.md) for build details, plant selection, and the time-discipline rules the host holds itself to.

### 6. Regenerate expectations

```bash
python -m tools.regenerate_expectations              # sim → specs/expectations/*.expected.json
uv run pytest tests/test_expectations.py             # committed artifact vs fresh run
uv run pytest tests/test_host_satisfies_expectations.py   # verdict equality vs the C++ trace
```

The expectations artifact is generated, never hand-authored.

## Project structure

```
relay/
├── spec/          Task spec loader and validation
├── generator/     Task spec validation, task spec → ST (deterministic compiler)
├── st/            Structured Text subset interpreter and function blocks
├── runtime/       PLC coroutine, scan loop, comm bus
│   └── harness.py  Simulation entry point: wires plant + comm + PLCs and drives the scan loop
├── plant/         Plant physics models (currently: conveyor)
├── strategies/    Stage-neutral leaf modules: comm registry, plant registry,
│                  assertion grammar parser, ST static-syntax helpers
├── verify/        Trace log and assertion evaluator (EVENTUALLY, PRECEDES, CAUSES)
├── clock.py       SimClock
├── io_image.py    Immutable per-scan I/O snapshot
├── trace.py       ScanRecord, Receipt, TraceLog
├── trace_io.py    Trace JSONL wire format (normative for the C++ host)
└── verdict_io.py  Verification verdicts as inspectable JSON
host/               C++23 deployment host (see host/README.md)
tools/              Language-boundary utilities: host input emission,
                    plant socket server, expectations regeneration
specs/              Task spec YAML examples
└── expectations/   Sim-certified verdict artifacts
tests/              End-to-end scenario tests and cross-language conformance
docs/
├── invariants/     Subsystem invariants
└── protocol/       Plant socket wire protocol
```

The pipeline flows left-to-right through the subsystem list: `spec/` → `generator/` → `st/` → `runtime/` + `plant/` → `verify/`. The `runtime/`, `plant/`, and `verify/` subsystems form a coupled surface — a change to scan-cycle structure or I/O image layout in one must be checked against the other two.

`relay/strategies/` sits outside that pipeline as a stage-neutral leaf layer. It holds registries and small parsing helpers that more than one stage needs to import — the comm strategy registry (used by both the spec validator and the runtime harness), the plant registry (same), the assertion grammar parser, and ST static-syntax helpers shared by the generator and interpreter. Routing these through a leaf module keeps imports flowing strictly forward through the pipeline; see [docs/invariants/pipeline_direction_imports.md](docs/invariants/pipeline_direction_imports.md). The simulation entry point is `relay/runtime/harness.py:simulate(spec, st_blocks)` — this is what the conveyor test calls.

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

## Why this exists

Relay is a personal project — a portfolio piece and a place to work out an architectural pattern, not a product. There is no roadmap to commercialize it and no user to serve but me.

The pattern is the point:

```
intent → reviewable specification → executable artifact → deterministic simulation → trace-based evidence
```

PLCs are a good domain to work it out in, because scan cycles make the timing semantics explicit and inter-PLC messaging makes the coordination problem concrete. But nothing in the shape is PLC-specific. The same chain applies to ECUs (requirements and modes as the spec, AUTOSAR components or state-machine code as the artifact, a deterministic scheduler with CAN and sensor models as the simulation, traces checked for deadlines and ordering as the evidence), and to robotics, embedded control, and distributed systems generally. It fits discrete and supervisory behavior best — mode management, interlocks, power sequencing, gateway logic. It is not sufficient on its own where continuous-time physics, calibration, and hardware-in-the-loop dominate.

[docs/whitepaper-draft.md](docs/whitepaper-draft.md) works out the general form: when correctness is certifiable at the IR, and when it is only certifiable through execution. Relay is the worked example of the second case.

Scope is deliberately narrow. One conveyor scenario carried end-to-end — through a real ST compiler, a real C++ host, a real socket boundary, with failure traces you can read — is worth more than a broad promise of an industrial automation platform.

## Requirements

- Python 3.11+
- For the C++ host: GCC 13+ or clang 17+, CMake ≥ 3.22
- See [`pyproject.toml`](pyproject.toml) for the full dependency list

## License

Copyright © 2026 Stephen S. Quinlan. All rights reserved.

This repository is published for portfolio and review purposes. No license is granted to use, copy, modify, or distribute this code or its contents. If you're interested in using any part of this work, please get in touch.

## Glossary

- **PLC** — Programmable Logic Controller. A ruggedized industrial computer that runs a fixed control program in a tight scan loop (typically every few milliseconds).
- **IEC 61131-3 Structured Text (ST)** — a standardized, Pascal-like programming language for PLCs.
- **Task spec** — relay's semantic intermediate representation: a YAML document capturing what PLCs exist, what they own, how they behave, and what temporal assertions must hold.
- **Function block** — a reusable unit of ST code that executes each scan and carries internal state (e.g., timer accumulators) between scans.
- **Scan** — one cycle of a PLC's main loop. Inputs are sampled, logic runs, outputs are written.
- **SimClock** — relay's injected external clock. All scan-cycle timing flows through SimClock, making runs deterministic and replay-able.
- **Receipt** — what a message carried, recorded where it was delivered: the sender, that sender's per-key sequence number, and the value. What `CAUSES` reads to attribute an effect to a cause.
- **Expectations artifact** — the sim-certified verdict for a spec, written to `specs/expectations/`. The contract every downstream environment is judged against.
- **Oracle** — the Python simulator in its role as the environment that decides what is true, against which every less-controlled environment is measured.
