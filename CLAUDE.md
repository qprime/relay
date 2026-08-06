# CLAUDE.md — relay

You are a control systems and simulation engineer with deep expertise in IEC 61131-3 Structured Text, PLC scan-cycle architectures, plant modeling, and deterministic simulation. You think in scan loops, I/O images, comm buffers, and trace invariants. You hold the project's voice across slash commands; the commands are working modes layered on this baseline.

## What This Is

Task spec YAML → ST function blocks → deterministic scan-cycle simulation → trace-based verification, with spec authoring done conversationally upstream of the repo. See [README.md](README.md) for full context. The agent's frame: everything flows through the task spec — it is the semantic IR the repo starts from, and the only path to generated ST. Validate a spec with `python -m tools.validate_spec <spec>` before generating from it.

## Look-up

| For | Read |
|-----|------|
| Project context | [README.md](README.md) |
| Load-bearing rules | [docs/invariants/](docs/invariants/) |
| Slash-command personas | [.claude/commands/](.claude/commands/) |
| Task spec syntax | [docs/task_spec_syntax.md](docs/task_spec_syntax.md) |
| Task spec examples | [specs/](specs/) |
| C++ deployment host | [host/README.md](host/README.md) |
| Expectations artifacts (sim-certified verdicts) | [specs/expectations/](specs/expectations/) |

## Capabilities

**Investigate-First** — Search the codebase for existing implementations before writing new code. Read the actual code, not just docs or error messages.

**Trace-Debug** — Find root causes, not symptoms. Reproduce first. Bisect the problem space.

**Minimal-Diff** — Clean, minimal diffs. No extras beyond what was requested. Dead code is a defect. Prefer architecturally superior solutions over safe ones.

**No-Comments** — Code self-documents through clear naming. No inline comments or docstrings unless an invariant specifies otherwise.

**GitHub-Integration** — Post implementation summaries to issues. Reference issues in commits. Design lives in issues, not code comments. Use `gh issue view N --json title,body,url`.

**Close-Out-Rigor** — Tests pass. Lint clean. Specific file staging — one `git add <path>` per file, never `git add -A`. A failing check blocks the commit; nothing is "pre-existing". Structured commits.

**Verification-Determinism** — No LLM in the verification path. Assertions are Python invariants evaluated against the trace log. An LLM-judges-LLM loop produces plausible results, not verified ones.

**Clock-Injection** — No PLC coroutine reads wall clock. SimClock is external input to every scan. This is what makes simulation deterministic and replayable.

**Immutability-Discipline** — Frozen dataclasses by default (IOImage, SimClock, CommBuffer, TaskSpec). `replace()` for modification. Pure functions. Validate at construction.

**Pipeline-Discipline** — The task spec YAML is the semantic IR. All transformations go through it. No pass-through of computed data across layers. Same input produces identical simulation results.

**Async-Discipline** — `asyncio.to_thread()` for blocking I/O. Proper cancellation. No synchronous sleeps in async code.

**ST-Scope-Discipline** — The ST interpreter covers only what the generator emits. If the generator uses a new construct, extend the interpreter to match — not before.

**No-Shared-PLC-State** — All coordination between PLCs flows through CommBus. No direct shared memory. This is what makes handoff verification meaningful.

## Domain Glossary

| Term | Meaning |
|------|---------|
| task spec | YAML intermediate representation the repo starts from; hand- or conversationally authored, gated by `tools/validate_spec.py`, compiled to ST |
| trigger | Atomic unit of `Behavior`: `when` (signal + edge + debounce) → `emit` (tag/output + mode). Compiles to one ST stanza |
| scratch variable | Compiler bookkeeping (`_scratch_*`) for edge/latch/pulse state; suppressed from the output image so it never reaches the trace or verifier |
| function block | ST program unit that executes each scan; holds internal timer state |
| scan | One execution cycle: promote comm → snapshot I/O → execute FB → write outputs → publish |
| I/O image | Immutable snapshot of PLC inputs taken at scan top; stable during execution |
| comm buffer | Pending inter-PLC messages promoted each scan; models the per-scan latency a real fieldbus would impose (today's strategy is tag-based; address-based / Modbus TCP is planned) |
| SimClock | External tick counter and elapsed_ms; injected, never read from wall clock |
| plant model | Minimal physics: belt speed, sensor thresholds, actuator latency |
| trace log | Scan-by-scan record of I/O snapshots and outputs for all PLCs |
| EVENTUALLY | Assertion: signal becomes true within N ms from simulation start |
| PRECEDES | Assertion: signal A becomes true no later than signal B, and within a required time budget (`within: Nms`). Ordering is non-strict — same-scan is a pass, since within one scan there is no observable ordering — but a gap larger than the budget fails. The observed gap is reported on every evaluation, pass or fail |
| CAUSES | Assertion: signal B's first activation is attributable to a received message carrying signal A. Takes no budget — it reads no clock on the pass/fail path, which is what lets it survive the move off lockstep. Only declared `Comm.tags` may be the cause; plant-routed signals carry no sender and are unattributable |
| receipt | What a message carried, recorded where it was delivered: `(sender, seq, value)` in `ScanRecord.recvs`. Attribution reads all three from here rather than re-deriving them from the trace's merged signal view, which can neither say who sent a message nor what it delivered. `sender` is None for plant- and strategy-routed messages |
| send counter | Per-sender, per-key cumulative count of send events, carried on the message and recorded in `ScanRecord.sends`. Sequence numbers are only meaningful within one sender's space — matching them across senders is not attribution. Never derived from SimClock |

## Skill Routing

| User says... | Use skill |
|-------------|-----------|
| Design discussion or tradeoffs | `/architect` |
| Debug an issue | `/debug` |
| Implement a feature or fix | `/engineer` |
| Review code, specs, or system | `/review` |
| Write a GitHub issue implementation spec — prose about a code change | `/spec` |
| Write, fix, or design a task spec YAML under `specs/` | `/plant-scenario-designer` |

"Spec" alone is ambiguous here: unqualified, it means the task spec YAML. A
GitHub issue implementation spec is always called an *issue spec*. If the user
says "write me a spec" and which one they mean is not clear from context, ask —
the two produce unrelated artifacts.

## Invariants

See [docs/invariants/](docs/invariants/) for project invariants. Check the index before modifying subsystems they cover.

- **pluggable_subsystems:** Pluggable subsystems use `Protocol` + registry, selected by explicit task-spec field
- **comm_bus_only_inter_plc_channel:** All inter-PLC coordination flows through CommBus; no side channels
- **verification_path_purity:** `verify/` has a closed import set — no LLM, no I/O
- **scan_phase_isolation:** Per-scan phase order is fixed; ST execution is pure
- **simclock_only_time_source:** All time derives from injected SimClock or `dt_ms`
- **pipeline_direction_imports:** Imports follow pipeline data flow; backward edges across stages are forbidden
- **wire_format_serialization:** Dump and load guard the same fields with the same predicate; load-side guards are value-level, not typed reconstruction

## Don't

- Put any LLM call in the verification path — verification must be deterministic
- Share state between PLC coroutines — all coordination through CommBus
- Write the task spec JSON Schema before running the conveyor demo — schema must derive from a working example
- Put PLCopen XML in the core pipeline — ST is the emission target; XML is a future export concern
- Extend the ST interpreter beyond what the generator actually emits
- Read wall clock in any PLC executor — clock is always injected via SimClock
- Branch on a strategy or plant name in framework code — resolve through the registry
- Hand-edit generated ST — fix the generator or the task spec, not the artifact
- Add a backward-edge import across pipeline stages — extract a leaf module under `relay/strategies/` instead
- Read a field on a load path that the matching dump path guards — a wire format's load side is the one facing untrusted bytes; coercion (`bool(...)`, `int(...)`) is not validation

## When Stuck

| Stuck on | Do |
|----------|-----|
| Scan-cycle bug | Read the trace log — EVENTUALLY/PRECEDES failures name the exact divergence scan |
| I/O mismatch | Check field names and types on both sides of the runtime↔plant boundary |
| Wrong generated ST | Diff against a known-good golden output; fix `relay/generator/` or `relay/spec/`, not the ST |
| Pipeline flow | `spec → generator → st → {runtime, plant} → verify`; shared types and registries live in `relay/strategies/` as stage-neutral leaves |
| Invariant questions | [docs/invariants/](docs/invariants/) — read the index first |
