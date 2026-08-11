# Approach to v1

**Status:** Complete — every step shipped | **As-of:** 2026-08-10
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

Four of five rungs on the validation chain are live. The Python sim certifies,
and the C++ host re-earns the verdict in-process, over a plant socket, and with
inter-PLC comm running over Modbus TCP. `CAUSES` is timing-free by construction
and survived the move off lockstep in
[#14](https://github.com/qprime/relay/issues/14).

The host is no longer only a runtime. Step 5 gave it a verifier, so the judge
now has two independent implementations and
`tests/test_cross_verifier_agreement.py` runs both over the sim's own trace.
Step 6 then gave it a fieldbus client. What remains Python-only is the front
half of the pipeline — spec, generator, ST emission — and the only rung left on
the chain is hardware rather than protocol.

---

## Open issues

| Issue | v1 disposition |
|-------|----------------|
| [#21](https://github.com/qprime/relay/issues/21) — `PRECEDES` cannot measure comm tag latency | **Closed** in `f7a7fcf`. A comm tag now resolves on its producer from `ScanRecord.sends`, anchored to the first truthy send. |
| [#22](https://github.com/qprime/relay/issues/22) — `CommBus::send` parks forever on a send to an exited PLC | **Closed** in `0e787b5`. Each PLC closes its own receive channel on exit; sends to a closed receiver are dropped and counted. |
| [#16](https://github.com/qprime/relay/issues/16) — Comm bus delivery latency, per-PLC periods, dead route pass | **Closed** in `c938056` + `eaa1a93`. Items 1 and 3 landed in v1 — see the note below on why the original "out of scope" ruling was wrong. Item 2 split to #23. |
| [#8](https://github.com/qprime/relay/issues/8) — Replace unmeasured timing budgets with measured ones | **Closes in v1.** Three unmeasured budgets in `specs/`. Both blockers are now clear, and the `PRECEDES` gap is a real 10.0ms. |
| [#26](https://github.com/qprime/relay/issues/26) — `address` strategy and routing on the `CommStrategy` protocol | **Closed.** Step 6a. Made `pluggable_subsystems` true for comm rather than cited, and was the prerequisite that kept 6b to one new variable. |
| [#23](https://github.com/qprime/relay/issues/23) — Per-PLC scan periods | **Out of scope.** No v1 consumer; payoff is the real-hardware story. Split out of #16. The one thing that could have pulled it in — 6b's poll interval — was settled at the consumer's scan top in [#27](https://github.com/qprime/relay/issues/27), which keeps it out. |
| [#27](https://github.com/qprime/relay/issues/27) — Modbus TCP transport | **Closed.** Step 6b. Settled the poll interval at the consumer's scan top and put a real wire protocol — 0x01 and 0x05 over MBAP — under the register map #26 declares. |
| [#17](https://github.com/qprime/relay/issues/17) — Real-hardware deployment target | **Out of scope.** Sequences behind Modbus. |
| [#28](https://github.com/qprime/relay/issues/28) — CAN as a third comm strategy | **Out of scope.** Opened during Step 6, after this plan was written. It is the natural second consumer of 6a's projection and the test of whether that abstraction was real — broadcast routing has no `produced_by` to lean on, and arbitration latency is not one consumer scan. Post-v1. |

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

### Step 2 — Visualization tool ✅ done (`bb37e07`)

**Spec:** [#24](https://github.com/qprime/relay/issues/24).
**Closes:** [#24](https://github.com/qprime/relay/issues/24). The collector half
of [#8](https://github.com/qprime/relay/issues/8) landed separately with
`cfcb95a` as `tools/observed_timings.py`; the renderer consumes traces rather
than rebuilding aggregation.

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

The landed renderer (`tools/render_report.py`) presents these panes without the
click-to-highlight cross-linking sketched above — that interaction was scoped
out of #24. The trigger provenance markers and the verifier's witness sentences
carry the same threads statically.

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

- Lives in `tools/`. Not on the verification path, and it must not widen
  `relay/verify/`'s closed import set (`verification_path_purity`) — it imports
  *from* `relay/verify/`, the allowed direction.
- Verdicts are evaluated from traces: the sim lane runs `simulate()` in-process,
  the host lanes load their trace JSONL through the guarded reader. The
  committed `specs/expectations/*.expected.json` are never a verdict source — a
  page built from always-green fixtures can never show a failure, which is the
  wrong-implementation #24's catcher test exists to block.

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

### Step 4 — Replace the unmeasured budgets ✅ done (`cfcb95a`)

**Spec:** none needed — [#8](https://github.com/qprime/relay/issues/8) is already
a complete spec.
**Closes:** [#8](https://github.com/qprime/relay/issues/8).

Three budgets in `specs/` are guesses; only the `PRECEDES` one is labelled as
such. The two `EVENTUALLY` budgets are measurable today — `part_at_b` is a plant
route in `plc_b`'s own I/O image, unaffected by #21 or #16, and its witness
reads `290.0ms` against a 500ms budget.

Steps 3 and 3.5 have landed, so the `PRECEDES` number is now both correctly
resolved and physically meaningful. The cross-run collector landed alongside as
`tools/observed_timings.py`. Review the numbers and replace all three. Drop the
placeholder comment at [specs/conveyor_handoff.yaml:58](../specs/conveyor_handoff.yaml).
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

Two cautions carry forward. The conservative oracle is chosen per-assertion, not
per-system: the host measures `0.0ms` on the comm hop because it does not charge
delivery, so the `PRECEDES` budget derives from the **sim's** 10.0ms — but the
host is the *slower* side on plant transit, observing 300.0ms against the sim's
290.0ms, so the `EVENTUALLY` budgets derive from the **host's** number. And
`EVENTUALLY(part_at_b)` does *not* move with the handoff: its witness is set by
belt-A travel time and is insensitive to actuator latency, so do not expect the
two budgets to shift together.

Re-run `tools/regenerate_expectations` and confirm the ten-consecutive-run gate:
tightening a budget can newly fail it.

**Do not auto-derive.** #8 is right that a self-tuning budget asserts whatever the
system currently does, which is not a contract. Reporting informs a human
decision; the human makes it.

### Step 5 — C++ verifier ✅ done

**Spec:** [#25](https://github.com/qprime/relay/issues/25).
**Closes:** [#25](https://github.com/qprime/relay/issues/25).

`relay_host_verify` reads a trace JSONL, evaluates the spec's assertions, and
writes `relay/verdict_io.py`'s document. Because it reads the wire format rather
than in-memory state it verifies the *sim's* trace, so
`tests/test_cross_verifier_agreement.py` holds the trace fixed and leaves the
verifier as the only variable. Purity is a property of the link graph — see
[host_verification_path_purity](invariants/host_verification_path_purity.md).

Both sides gained structured `attribution` on a passing `CAUSES`, and both
replaced first-in-list-order selection with minimum-by-`(elapsed_ms, plc_id)`:
"first in the list" names a property of the container, and a second
implementation cannot be independently correct about it.

The original framing, kept because it is what the port had to get right:

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

**Invariant implication:** resolved as a host-side sibling rather than an
amendment. `verification_path_purity` is built on a Python import allowlist and
the edge along which a model client would arrive; the C++ constraint is checked
by what the linker accepts. One document stating both in two languages would
state neither precisely.

### Step 6 — Modbus TCP, split in two

The largest item in v1, and the one place the plan changed shape after Step 5
shipped. Writing the spec revealed that "Modbus TCP comm strategy" was two
changes wearing one name: making the framework capable of a second comm idiom
at all, and putting a real wire protocol underneath one. Landing them together
would put the register binding and the wire format on the same rung of the
validation chain, which is the failure [README.md](../README.md) says the chain
exists to prevent.

The split is what keeps 6b to one new variable.

#### Step 6a — The `address` strategy and routing on the protocol

**Spec:** [#26](https://github.com/qprime/relay/issues/26).
**Closes:** the `address` strategy stub.

`CommStrategy` grows from validate-only to also projecting the comm block into
`(name, produced_by, consumed_by)` signals, and the five framework sites that
read `comm_block["tags"]` directly — codegen, Behavior validation, the comm
signal-name set, the `CAUSES` pre-check, and the language boundary — reroute
through that projection. `address` is then implementable over a register map.

This is what makes `pluggable_subsystems` true for comm rather than cited. The
invariant appears in four documents and has zero second implementations today,
and [host/src/comm_strategy.cpp:57](../host/src/comm_strategy.cpp#L57) is a
literal `if (spec.comm.strategy == "tag")`. The proof obligation is an
address-idiom port of the conveyor handoff whose generated ST is byte-identical
to the tag version's.

No sockets, no framing, no transport. `table` and `address` are declared and
validated here and consumed by nothing until 6b.

#### Step 6b — Modbus TCP transport — **done**

**Spec:** [#27](https://github.com/qprime/relay/issues/27).

Shipped: 0x01 and 0x05 over MBAP framing, a `ModbusTcpTransport` under
`CommBus`, [tools/modbus_server.py](../tools/modbus_server.py) as the register
file, and a loopback test proving `conveyor_handoff_address` earns the same
verdicts over TCP that it earns in-process. The subset is decided by what the
generator can emit — every emit mode assigns a boolean, so word access is
protocol surface no spec can drive, and `address` now accepts `coil` alone.
[docs/protocol/modbus_tcp.md](protocol/modbus_tcp.md) is normative.

**The poll interval settled at the consumer's scan top, not a third rate.** #26
deferred the question; three invariants converge on the answer —
`scan_phase_isolation` makes phase 2 the only entry point for inter-PLC data,
`simclock_only_time_source` forbids a fourth pacing source, and #16's delivery
phrasing is satisfied by construction when the poll *is* the scan top. That is
what keeps [#23](https://github.com/qprime/relay/issues/23) post-v1: per-PLC
periods change when each consumer polls and nothing else, so the delivery rule
needs no amendment.

The design problem was attribution. Modbus has no field for a sender and none
for a sequence number, so a receipt is reassembled from three sources: the
sender from the declared `produced_by`, the seq from the transport's own record
of the last acknowledged write, the value from the register. The pairing cannot
be atomic over a wire that carries only the value, so the poll reads the map
before the register and the stamp runs behind rather than ahead — the safe
direction, since the verifier matches on `count >= seq`.

Transport selection is a host concern, chosen by a flag the way
`--plant-endpoint` selects `remote_socket` — not a resurrection of the C++
strategy switch 6a deleted.

### Step 7 — Close-out ✅ done

**Spec:** none.

- **README validation chain.** Modbus TCP is now a fifth rung between the plant
  socket and hardware, and the last rung was renamed from "real fieldbus" to
  "real hardware" — a real fieldbus is what 6b shipped. What #17 adds is
  physical I/O and a device's timing, not a protocol. The scope-boundaries table
  and the `host/README.md` headroom table were both settled in 6b, the latter
  measuring the Modbus column identical to the in-process one across ten runs
  (300.0ms / 0.0ms).
- **`host/README.md` interim assumption register.** One new row, still open:
  *one host process owns every PLC endpoint*, which is what lets a Modbus
  receipt's `seq` be synthesized from the emit side's acknowledged-write map.
  The protocol doc already says the technique does not generalize; the register
  is where that stops being a footnote. Guarded by `validate_comm_signals`,
  which keeps `sender` from coming out transport-dependent.
- **Gate re-run.** `tools/regenerate_expectations` reproduced all three
  artifacts byte-identical, and the suite passes including the
  ten-consecutive-run gate.
- **Issues.** #21, #22, #16, #25, #26, and #27 are closed. #23 (per-PLC
  periods), #17 (real-hardware target), and #28 (CAN strategy) stay open as
  post-v1.
- `docs/task_spec_syntax.md` already states which side of a comm tag is visible
  to assertion resolution (#21) and that a cross-PLC budget must exceed one
  consumer scan period (#16).

**One thing this step surfaced and did not close.** The checkpoint report
renderer builds three lanes — `sim`, `host`, `host-socket` — and has no Modbus
lane, so v1's fieldbus rung is invisible in the one artifact built to make the
project legible to a non-expert. Adding a fourth lane gated on the spec's comm
strategy being `address` is a small change to `tools/render_report.py`; it is
code rather than close-out and did not get folded in here.

---

## Spec schedule

Every step that needed a spec got one, and all of them shipped.

| Step | Spec | State |
|---|---|---|
| 2 | [#24](https://github.com/qprime/relay/issues/24) | shipped |
| 5 | [#25](https://github.com/qprime/relay/issues/25) | shipped |
| 6a | [#26](https://github.com/qprime/relay/issues/26) | shipped |
| 6b | [#27](https://github.com/qprime/relay/issues/27) | shipped |
| 7 | none needed | shipped |

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
| Validation chain rungs live | 3 of 4 | 4 of 5 |
| Open issues | 5 | 3 (#23, #17, and #28, opened during v1) |

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
- **[#28](https://github.com/qprime/relay/issues/28) — CAN as a third comm
  strategy.** The real test of 6a's projection, because CAN breaks both things
  `address` could still lean on: a broadcast frame has no `produced_by` to name
  a sender, and arbitration latency is not one consumer scan. If
  `(name, produced_by, consumed_by)` survives that, the abstraction was real.
- **A second plant.** `pluggable_subsystems` now has a second comm strategy
  behind it, but the plant registry still holds one entry and all three specs in
  `specs/` are conveyor variants. A plant with a different sensor vocabulary
  would prove the plant registry is a registry too.
- **`NEVER` / `ALWAYS` assertion forms.** The grammar cannot express safety
  properties — you cannot say "the gate never opens while the press is down."
  That is the more important half of the property space for a control-systems
  verifier.
- **A Modbus lane in the checkpoint report.** `tools/render_report.py` builds
  `sim`, `host`, and `host-socket`; the fieldbus rung v1 added has no column.
  The smallest item on this list and the one that most changes what a reader
  sees, since the report is the artifact a non-expert judges the project by.
- **Export adapters** — OpenPLC, CODESYS, PLCopen XML, Factory I/O, PLCverif.
  Named in the README as obvious directions, all absent.
