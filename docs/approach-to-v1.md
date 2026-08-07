# Approach to v1

**Status:** Plan | **As-of:** 2026-08-07
**Purpose:** Define what v1 is, what gets built to reach it, and what is left for later.

---

## What v1 is

v1 is a **stopping place** — the point at which relay gets set down to make room
for other experiments. Three things define it:

1. **The open issues that must not survive it are closed.**
2. **The C++ host stops being only a runtime** — it gains a verifier and a
   fieldbus client.
3. **There is a visualization tool** that makes the pipeline legible to a reader
   who does not know what a scan cycle is.

Everything else is out of scope. See [What's next](#whats-next) for reference.

---

## Where things stand

Three of four rungs on the validation chain are live. The Python sim certifies,
and the C++ host re-earns the verdict in-process and over a socket. `CAUSES` is
timing-free by construction and survived the move off lockstep in
[#14](https://github.com/qprime/relay/issues/14).

The C++ host is currently *only a runtime* — parser, evaluator, scan executor,
comm bus, plant adapter. The front half of the pipeline and the judge are
Python-only. That asymmetry is what v1 corrects, and it is corrected by moving
pipeline stages into C++ rather than by adding features to the runtime.

---

## Open issues

| Issue | v1 disposition |
|-------|----------------|
| [#21](https://github.com/qprime/relay/issues/21) — `PRECEDES` cannot measure comm tag latency | **Closed** in `f7a7fcf`. A comm tag now resolves on its producer from `ScanRecord.sends`, anchored to the first truthy send. |
| [#22](https://github.com/qprime/relay/issues/22) — `CommBus::send` parks forever on a send to an exited PLC | **Closed** in `0e787b5`. Each PLC closes its own receive channel on exit; sends to a closed receiver are dropped and counted. |
| [#16](https://github.com/qprime/relay/issues/16) — Comm bus delivery latency, per-PLC periods, dead route pass | **Closed** in `c938056` + `eaa1a93`. Items 1 and 3 landed in v1 — see the note below on why the original "out of scope" ruling was wrong. Item 2 split to #23. |
| [#8](https://github.com/qprime/relay/issues/8) — Replace unmeasured timing budgets with measured ones | **Closes in v1.** Three unmeasured budgets in `specs/`. Both blockers are now clear, and the `PRECEDES` gap is a real 10.0ms. |
| [#23](https://github.com/qprime/relay/issues/23) — Per-PLC scan periods | **Out of scope.** No v1 consumer; payoff is the real-hardware story. Split out of #16. |
| [#17](https://github.com/qprime/relay/issues/17) — Real-hardware deployment target | **Out of scope.** Sequences behind Modbus. |

**Correction to the original #16 ruling.** This plan first marked #16 out of scope
on the reasoning that "the zero-latency bus and the schema work both sequence
behind Modbus." That conflated three unlike items. The dead route pass had no
dependency on anything and was pure deletion. The delivery-latency fix is small,
leaves the host untouched, and — decisively — changes the numbers #8 freezes into
budgets: measuring on a zero-latency bus derives budgets from an *optimistic*
oracle, the exact failure Step 4 below warns against. Only per-PLC periods was
correctly deferred, and it is now #23. Modbus models fieldbus transport in the
host; the sim's promotion semantics are an independent surface.

---

## The work

### Step 1 — Fix the `CommBus::send` backpressure hang ✅ done (`0e787b5`)

**Spec:** none needed — the analysis is already written in
[#22](https://github.com/qprime/relay/issues/22).
**Closes:** [#22](https://github.com/qprime/relay/issues/22), the hard
prerequisite of #23.

Landed as shape A: each PLC closes its own receive channel as its scan loop
exits, and a send addressed to a closed receiver is dropped and counted rather
than queued. `plcs_done` could not serve as the liveness signal — it is a bare
count and never says *which* PLC exited, which is why the plant loop's
`plcs_done == plc_count` guard left the hole open. Drops surface per consumer on
stderr via `host_main`.

Note for #23: the conveyor run now reports one dropped message per run, a plant
route delivered after `plc_b`'s final scan. That drop was always happening and
was silently discarded by the old teardown. It is the live signal for whether a
due-time scheduler sheds messages it should not.

### Step 2 — Visualization tool

**Spec:** `/spec` before implementation. **Spec this first** — it gates Step 4.
**Closes:** the collector half of [#8](https://github.com/qprime/relay/issues/8).

A tool in `tools/` that renders one spec run as a single self-contained HTML page:
intent, task spec, generated ST, trace, and verdict, cross-linked so that clicking
an assertion highlights the spec clause it constrains, the ST stanza compiled from
that clause, and the exact scan records the verdict cites.

```
┌─ intent ────────────┬─ task spec ──────────────┐
│ "When A's exit      │  triggers:               │
│  sensor sees a      │    - id: handoff_on_exit │
│  part, signal B..." │      when: {...}   ◄─────┼── highlighted
├─ generated ST ──────┼─ trace ──────────────────┤
│ (* trigger:         │  tick 10  plc_a          │
│  handoff_on_exit *) │    sends handoff seq 11  │
│ _scratch_edge... ◄──┼─ tick 10  plc_b          │
│                     │    recv  handoff ◄───────┼── the receipt
└─────────────────────┴──────────────────────────┘
  VERDICT  ✅ CAUSES(handoff_signal, belt_b_enable)
     "...is caused by 'handoff_signal' seq 11 sent by 'plc_a'..."
```

Two threads already exist and should be used rather than rebuilt:

- **Spec → ST** is threaded by the provenance markers from
  [#11](https://github.com/qprime/relay/issues/11) —
  `(* trigger: handoff_on_exit *)`. This is what those markers are *for*.
- **Trace → verdict** is threaded by the witness strings the verifier already
  writes on the pass path. `"'belt_b_enable' true on 'plc_b' at tick 10 is caused
  by 'handoff_signal' seq 11 sent by 'plc_a' at tick 10 and received at tick 10"`
  is already a layman-readable sentence.

In the [whitepaper](whitepaper-draft.md)'s vocabulary this is a **checkpoint
rendering** — a representation of the system's work-so-far in a form a reader can
judge against intent without expert review of the layers above. The whitepaper
argues checkpoint topology is the principal architectural decision in a compiled
system; relay has had the topology and no rendering of it.

**Constraints:**

- Lives in `tools/`, reads committed artifacts. Not on the verification path, and
  it must not widen `relay/verify/`'s closed import set
  (`verification_path_purity`).
- #8 explicitly warns against bolting a printer onto `evaluate_all`. The tool
  reads the verdict JSON and trace JSONL as files.
- It must aggregate observed timings across runs — that is the collector #8 asks
  for. Per-run collection already exists (`relay/verdict_io.py` from #13, and the
  committed `specs/expectations/*.expected.json`); what is missing is aggregation
  across runs, since each run overwrites one artifact in place.
- The two assertion forms expose their timings asymmetrically. `PRECEDES` has the
  structured `observed_gap_ms` field; `EVENTUALLY`'s witness time exists only
  inside the prose `reason` string. Decide here whether `AssertionResult` should
  carry a structured witness time, rather than parsing prose.

**Note on process:** this is the artifact a non-expert judges the project by, and
much of what makes it good is visual judgment the spec cannot settle. Expect to
iterate on the rendered page rather than on the spec.

### Step 3 — Make comm tag latency measurable ✅ done (`f7a7fcf`)

**Spec:** decided in conversation rather than a separate issue spec; the
reasoning is recorded on [#21](https://github.com/qprime/relay/issues/21).
**Closes:** [#21](https://github.com/qprime/relay/issues/21), and unblocks the
`PRECEDES` half of [#8](https://github.com/qprime/relay/issues/8).

Landed as shape **B1** — `ScanRecord.sends` carries `(count, value)`, and a comm
tag resolves on its producer from `sends`, anchored to the first *truthy* send.
B2 was rejected: it measures a delivery and names the sender's scan, so a send's
timestamp would depend on who consumed it and whether anyone did. Those coincide
only while the bus charges zero latency, which Step 3.5 then fixed — B2 would
have built the instrument out of the measurand.

Two things beyond the issue's stated scope were fixed here: `PRECEDES` naming two
producer-side signals used to resolve each on a different PLC silently, and
`EVENTUALLY` was moved to the same resolution helper so one name cannot mean the
producer's emission in one form and the consumer's delivery in another.

Before Step 5 because the C++ verifier port must inherit the fixed rule.

### Step 3.5 — Charge comm bus delivery latency ✅ done (`c938056`, `eaa1a93`)

**Spec:** none needed — [#16](https://github.com/qprime/relay/issues/16) is
already a complete spec for items 1 and 3.
**Closes:** [#16](https://github.com/qprime/relay/issues/16) (items 1 and 3;
item 2 split to [#23](https://github.com/qprime/relay/issues/23)).

**Before Step 4, not after.** Step 4 freezes measured numbers into contractual
budgets. Measured on the old zero-latency bus, those budgets would come from an
optimistic oracle — precisely what Step 4's own warning is about. Landing this
first means #8 measures once, against a conservative oracle.

The old bus delivered for free along `System.plcs` declaration order and one
scan against it, because a PLC's in-scan `bus.send` was readable by any consumer
whose coroutine had not yet run in the same harness iteration. `CommBus` now
stamps each message with the sending scan's `elapsed_ms` and delivers only
entries stamped strictly earlier than the consumer's scan top. Plant routes are
exempt — a sensor wired to the input terminals is sampled at scan top, not
delivered over a network.

`conveyor_handoff` now reports `observed_gap_ms == 10.0`. The host still reports
`0.0` and is deliberately unchanged: its clock referent is the wall clock and its
in-process channel models a backplane. The sim is therefore the conservative
oracle, and verdict equality — per-assertion pass/fail — is unaffected by the
differing gaps.

The dead `TagStrategy.route` pass was deleted in the same campaign; expectations
and the golden trace regenerated byte-identical, which is the evidence it was
dead.

### Step 4 — Replace the unmeasured budgets

**Spec:** none needed — [#8](https://github.com/qprime/relay/issues/8) is already
a complete spec.
**Closes:** [#8](https://github.com/qprime/relay/issues/8).

Three budgets in `specs/` are guesses; only the `PRECEDES` one is labelled as
such. The two `EVENTUALLY` budgets are measurable today — `part_at_b` is a plant
route in `plc_b`'s own I/O image, unaffected by #21 or #16, and its witness
reads `290.0ms` against a 500ms budget.

Steps 3 and 3.5 have landed, so the `PRECEDES` number is now both correctly
resolved and physically meaningful. Only Step 2's cross-run aggregation is still
outstanding. Review the numbers and replace all three. Drop the placeholder
comment at [specs/conveyor_handoff.yaml:58](../specs/conveyor_handoff.yaml).
There is no corresponding hedge in [README.md](../README.md) — it carries the
`500ms` literals bare at lines 85-86 with no commentary, so nothing there to
delete.

**The `PRECEDES` budget hedge is resolved.** This section previously warned that
the gap would be trustworthy but still zero, forcing a choice between documenting
a measured zero and authoring a scenario with real slack. Steps 3 and 3.5
together produced a real number: `conveyor_handoff` reports `10.0ms`, one
consumer scan period of charged delivery latency. State the budget against that
measurement with an explicit margin, and note that any cross-PLC budget must
exceed one consumer scan period by construction.

Two cautions carry forward. The host measures `0.0ms` on the same spec because it
does not charge the hop, so a budget must be derived from the **sim's** number —
the conservative one. And `EVENTUALLY(part_at_b)` does *not* move with the
handoff: its 290.0ms witness is set by belt-A travel time and is insensitive to
actuator latency, so do not expect the two budgets to shift together.

Re-run `tools/regenerate_expectations` and confirm the ten-consecutive-run gate:
tightening a budget can newly fail it.

**Do not auto-derive.** #8 is right that a self-tuning budget asserts whatever the
system currently does, which is not a contract. Reporting informs a human
decision; the human makes it.

### Step 5 — C++ verifier

**Spec:** `/spec` before implementation.

Port `relay/verify/` to the host so it evaluates its own trace and emits its own
verdict artifact. The expectations test then becomes **two independent verifiers
agreeing on one trace**, which is a materially stronger claim than one verifier
applied twice.

Python stays the oracle. The C++ verdict is corroborating, not authoritative —
the contract in [host/README.md](../host/README.md) is verdict equality per
assertion, and that does not change.

**The interesting part is `CAUSES`.** Reimplementing
[assertions.py:118-228](../relay/verify/assertions.py) forces you to re-derive
the three failure modes its docstring documents: an output shadowing a `False`
delivery, overlapping per-sender seq spaces, and binding to a receipt whose value
said nothing happened. A port that translates without re-deriving will reproduce
the bugs.

**Port the tag resolution rule, not just `_signal_value`.** Since Step 3 the
Python verifier resolves a comm tag on its *producer*, from `ScanRecord.sends`,
anchored to the first truthy send; every other name resolves `outputs` then the
I/O image. A port that reads only `_signal_value` reproduces the pre-#21 defect
on the C++ side, and verdict equality would hide it by having both verifiers
agree on the same wrong number.

**Expect the two verifiers to report different gaps on the same spec, and do not
"fix" that.** The C++ verifier reading the host's trace measures `0.0ms` on
`conveyor_handoff` where the Python verifier reading the sim's trace measures
`10.0ms`, because the host does not charge the delivery hop (Step 3.5). The
contract is verdict equality per assertion, not gap equality.

**Invariant implication:** `verification_path_purity` is a Python-side invariant
with a closed import set and a test. A C++ verifier needs the same guarantee
expressed in C++ terms, or the claim is weaker on that side. Either amend the
existing invariant to cover both languages or add a host-side sibling — decide in
the spec, not in the implementation.

### Step 6 — Modbus TCP comm strategy

**Spec:** `/spec` before implementation. The largest spec in this plan; write it
after Step 5 lands so the host's shape is settled.
**Closes:** the `address` strategy stub.

The `address` strategy is registered today and raises `NotImplementedError`.
Modbus TCP is the natural first real transport and the one that makes relay's
multi-protocol identity real rather than aspirational.

Register maps, coil and holding-register addressing, framing, a protocol with a
published specification to conform to. The spec should decide which subset of
Modbus is in scope — conformance to a defined subset, not coverage for its own
sake.

**Scope reaches back into the schema** — register maps have to be declarable in
the task spec, which means `relay/spec/`, the validator,
[tools/emit_host_inputs.py](../tools/emit_host_inputs.py), and the host spec
loader. It also probably wants a Python Modbus server for the loopback test,
mirroring what [tools/plant_server.py](../tools/plant_server.py) does for the
plant socket.

This is the largest item in v1.

### Step 7 — Close-out

**Spec:** none.

- README: update the validation-chain table and the scope-boundaries table
  (Modbus moves from "out of scope" to in).
- `host/README.md`: re-measure the headroom table under Modbus transport latency
  and update the interim assumption register. The `PRECEDES` row was already
  restated in Step 3.5 — it now records the sim's 10.0ms against the host's
  0.0ms and why that asymmetry is deliberate.
- Re-run `tools/regenerate_expectations` and confirm the ten-consecutive-run gate.
- #21, #22, and #16 are closed. #23 (per-PLC periods) stays open as post-v1.
- `docs/task_spec_syntax.md` already states which side of a comm tag is visible
  to assertion resolution (#21) and that a cross-PLC budget must exceed one
  consumer scan period (#16).

---

## Spec schedule

Two items still need a `/spec` issue before implementation.

| When to spec | Item | Why then |
|---|---|---|
| **Now** | Step 2 visualization tool | Gates Step 4's aggregation half. The only remaining v1 item with an open design. |
| After Step 5 ships | Step 6 Modbus TCP | Largest scope, reaches into the schema, wants the host's shape settled. |

Step 5's spec can be written any time before Step 5 starts. It must port Step 3's
resolution rule — a tag resolves on its producer from `sends`, anchored to the
first truthy send — not the pre-#21 rule, and it must account for Step 3.5's
delivery charge being a sim-side semantic the host does not share.

Steps 1, 3, 3.5, and 4 needed no separate spec: 1 and 3.5 had complete analyses in
#22 and #16, Step 3's one open decision was settled in conversation and recorded
on #21, and Step 4 is a data-review task with #8 as its spec. Step 7 is close-out.

Existing project rules apply throughout: implementation commits say `Refs #N`;
closing keywords only after code review.

---

## What v1 changes

| | Before | After |
|---|---|---|
| C++ role | runtime only | runtime + verifier + fieldbus client |
| Placeholder budgets | 2 | 0 |
| Known latent deadlocks | 1 | 0 |
| Visualization surfaces | 0 | 1 |
| Independent verifier implementations | 1 | 2 |
| Sim comm bus | zero latency, ordering-dependent | one consumer scan period, order-invariant |
| Open issues | 5 | 2 (#23, #17) |

---

## What's next

Reference only. None of this is v1.

- **[#17](https://github.com/qprime/relay/issues/17) — real-hardware target.** The
  headline next step, sequenced behind Modbus. Carries the one genuinely open
  design question in the project: what records a `ScanRecord` when the scan runs
  on hardware the harness does not own?
- **[#23](https://github.com/qprime/relay/issues/23) — per-PLC scan periods.**
  What remains of #16 after items 1 and 3 landed in Steps 3.5. The host executor
  API is already per-context — `test_plcs_reach_different_ticks` runs two
  executors at 1ms/40ms — leaving schema, the validator, a due-time scheduler in
  the Python harness, `emit_host_inputs`, and the host loader. The delivery
  charge landed in Step 3.5 is phrased as one *consumer* period precisely so it
  survives this unchanged. #22's drop counter is the live signal for whether the
  new scheduler sheds messages it should not.
- **A second plant.** `pluggable_subsystems` is claimed in four places and
  demonstrated in zero — the registry holds one entry and both specs in `specs/`
  are conveyor variants. A plant with a different sensor vocabulary would prove
  the registry is a registry.
- **`NEVER` / `ALWAYS` assertion forms.** The grammar cannot express safety
  properties — you cannot say "the gate never opens while the press is down."
  That is the more important half of the property space for a control-systems
  verifier.
- **Export adapters** — OpenPLC, CODESYS, PLCopen XML, Factory I/O, PLCverif.
  Named in the README as obvious directions, all absent.
