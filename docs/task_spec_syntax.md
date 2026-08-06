# Task spec syntax

The task spec is the semantic IR this repo starts from. Everything downstream —
generated ST, the simulation, the verifier's verdicts — is derived from it, so a
spec that is legal but says the wrong thing produces legal-looking wrong ST.

This manual is written against the validator as it is. If the manual and the
code disagree, the code wins and the manual is the bug. Every complete example
below is extracted and run through `tools.validate_spec` by
[`tests/test_task_spec_syntax_doc.py`](../tests/test_task_spec_syntax_doc.py), so
an example that stops validating fails CI.

Validate a spec before generating from it:

```
python -m tools.validate_spec specs/conveyor_handoff.yaml
```

Worked examples: [`specs/conveyor_handoff.yaml`](../specs/conveyor_handoff.yaml)
(latched handoff) and
[`specs/conveyor_pulse_release.yaml`](../specs/conveyor_pulse_release.yaml)
(debounced edge, pulsed output).

## What this manual covers

A spec has five top-level blocks. Two of them are strategy-owned and two are
not, and the split governs what is documented here:

| Block | Owner | Documented here |
|---|---|---|
| `System` | framework | Fully |
| `Behavior` | framework | Fully — the trigger IR |
| `Assertions` | framework | Fully — all three forms |
| `Comm` | the strategy named by `Comm.strategy` | Shape contract only |
| `Plant` | the plant named by `Plant.type` | Shape contract only |

`System`, `Behavior`, and `Assertions` are identical whichever comm strategy or
plant type a spec declares, so they have a single authority and it is this file.

`Comm.tags`, `Plant.config`, `Plant.routes`, and `Plant.actuators` are
**strategy-owned**: their fields are whatever the selected strategy's
`validate_config` accepts, and a spec carries one strategy's idiom rather than
the union of every strategy's fields
([`docs/invariants/pluggable_subsystems.md`](invariants/pluggable_subsystems.md),
clause 6). Transcribing those field lists here would put a second, drifting copy
of them next to the validator that owns them. For those blocks, read
`validate_config` on the strategy you declared — `TagStrategy.validate_config`
in [`relay/strategies/comm.py`](../relay/strategies/comm.py),
`ConveyorPlant.validate_config` in
[`relay/plant/conveyor.py`](../relay/plant/conveyor.py) — and iterate against
`python -m tools.validate_spec`, which is what makes authoring converge.

What the framework *does* guarantee about those blocks, and what the rest of the
spec depends on, is the shape contract:

- `Comm.strategy` and `Plant.type` are registry lookups. An unregistered name is
  terminal: validation stops, because no other check is meaningful until the
  selector resolves. Registered comm strategies are `tag` (live) and `address`
  (a stub that raises). The Python plant registry holds `conveyor` alone.
- Whatever the plant's route entries call `as_key`, those names are the signals
  a PLC can read from the plant.
- Whatever the comm block declares as tags, those names are the signals PLCs can
  send each other, and only those names can be a `CAUSES` cause.
- Whatever the plant's actuator entries name as `key` are the outputs a PLC
  writes that the plant reads back.

## `System`

```yaml fragment
System:
  name: conveyor_handoff
  plcs:
    - id: plc_a
      role: upstream
    - id: plc_b
      role: downstream
```

`name` and every `plcs[].id` must match `[a-z][a-z0-9_]*`. `name` is what the
expectations artifact is filed under (`specs/expectations/<name>.expected.json`),
so it must be unique across `specs/` — `tools.regenerate_expectations` refuses to
write anything when two specs claim one name, since whichever the glob reached
last would otherwise overwrite the other's certified verdicts. `role` is required
and is free-form prose; nothing downstream reads it.

The declared `id`s are the only valid PLC names anywhere else in the spec —
`Behavior` keys, `produced_by` / `consumed_by`, `to_plc`, `from_plc`.

## `Behavior` — the trigger IR

`Behavior` maps each PLC id to a list of triggers. Each trigger compiles to
exactly one ST stanza, carrying a `(* trigger: <id> *)` provenance marker.

```yaml fragment
Behavior:
  plc_a:
    triggers:
      - id: handoff_on_exit
        when:
          signal: sensor_a_exit
          edge: rising
          debounce_ms: 0
        emit:
          tag: handoff_signal
          mode: latched
```

| Field | Values | Meaning |
|---|---|---|
| `id` | `[a-z][a-z0-9_]*`, unique within the PLC | Names the stanza; scratch variables are suffixed with it |
| `when.signal` | string | A plant route `as_key` targeting **this** PLC, or a comm tag **this** PLC consumes |
| `when.edge` | `rising` \| `falling` \| `level` | Which transition fires the trigger |
| `when.debounce_ms` | int ≥ 0, default 0 | Stability window; see below |
| `emit.tag` \| `emit.output` | string, **exactly one** | A tag this PLC produces, or a local output name |
| `emit.mode` | `latched` \| `pulse` \| `steady` | How the target holds |
| `emit.duration_ms` | int > 0 | Required when `mode: pulse`, rejected otherwise |

There is no free-form rule text and no `owns` list. Signal ownership derives from
`Plant.routes[].to_plc` and the comm block's producer/consumer declarations, so
every timing decision a scenario depends on — edge, debounce window, pulse
width — must be written in these fields. A primitive these fields cannot express
is a framework extension, not something to describe in prose.

### `debounce_ms` shifts the edge, not only the timing

Debounce compiles to a TON on the **raw** signal, and the edge detector reads the
timer's `.Q` rather than the signal
([`relay/generator/behavior.py`](../relay/generator/behavior.py)):

```
_scratch_debounce_<id>(IN := <signal>, PT := T#<debounce_ms>ms);
_scratch_stable_<id> := _scratch_debounce_<id>.Q;
_scratch_edge_<id> := _scratch_stable_<id> AND NOT _scratch_prev_<id>;
```

So `rising` with `debounce_ms: 20` does not fire at the raw transition and then
wait — it fires when the signal has *already been high for 20 ms*. On a 10 ms
scan period that is two scans later than the same trigger with no debounce. A
`PRECEDES` budget written against the undebounced timing will be short by the
debounce window.

`level` with a debounce is the same TON with no edge detector: the target follows
`.Q`, true while the signal has been continuously high for the window.

### Modes

- **`steady`** — the target follows the trigger condition down as well as up.
  Combined with `edge: rising` this is a one-scan spike, since a rising edge is
  true for exactly one scan.
- **`latched`** — the target is set once and holds. There is **no reset
  primitive**: a latch never clears for the remainder of the run. If a scenario
  needs a signal to drop, `steady` or `pulse` are the only options.
- **`pulse`** — the target asserts on the condition and clears after
  `duration_ms`, via a TON on the pulse variable. A second condition during the
  window does not extend it.

### Rules the validator enforces

- **One trigger per emit target per PLC.** Two triggers writing the same tag or
  output on one PLC is rejected; the second assignment would silently win.
- **`_send_` and `_scratch_` are reserved prefixes** on `emit.output`.
  `_send_*` routes comm tags; `_scratch_*` is compiler bookkeeping and is
  suppressed from the output image so it never reaches the trace or verifier.
- **`emit.output` may not collide with a plant route `as_key`.** Assertion
  resolution prefers `outputs` over the I/O image across every PLC, so an output
  sharing a sensor's name would mask the sensor and assertions would pass on the
  emitted value rather than on what the plant reported.
- **`emit.output` may not collide with a declared tag name.** If this PLC
  produces that tag, write `tag:`; otherwise rename the output.
- **`when.signal` must be readable by this PLC** — a route targeting it or a tag
  it consumes. A tag this PLC *produces* is not readable by it.
- **`emit.tag` must be produced by this PLC**, per the comm block.

## `Assertions`

Assertions are strings in a fixed grammar
([`relay/strategies/assertions.py`](../relay/strategies/assertions.py)),
evaluated against the trace log by
[`relay/verify/assertions.py`](../relay/verify/assertions.py). No LLM is in this
path. Every signal named must resolve to a trigger emit target, a plant route
`as_key`, or a declared tag — validation rejects a spec that asserts on a name
nothing produces.

| Form | Asserts | Budget |
|---|---|---|
| `EVENTUALLY(signal, within: Nms)` | The signal is true in some scan at or before N ms from simulation start | Required |
| `PRECEDES(a, b, within: Nms)` | `a` first becomes true no later than `b`, and `b_ms - a_ms` fits the budget | Required |
| `CAUSES(a, b)` | `b`'s first activation is attributable to a received message carrying `a` | None — rejected if given |

Signal resolution depends on what kind of signal the name denotes.

A **comm tag** resolves on its **producer**, from the send the producer
recorded — not from the consumer's I/O image where it is delivered. A tag is
emitted in one place and delivered in others, and the name denotes the
emission. The value is read from the send too, so a producer that sends every
scan carrying `False` anchors to its first *truthy* send rather than to its
first message. A declared tag that no trigger emits resolves to nothing and is
rejected at validation.

Every **other** signal resolves `outputs` first, then the I/O image, searched
across every PLC's records. A signal need not be in the output image; a
plant-routed input resolves too.

The split matters for cross-PLC assertions. `PRECEDES(sensor_a_exit,
handoff_signal, within: 500ms)` reads as a producer-side latency claim, and it
is one: both names resolve on the producer. Before the tag rule, the tag
resolved off the *consumer's* image and the assertion silently measured a
delivery instead.

### `PRECEDES`

Ordering is **non-strict**: both signals becoming true in the same scan is a
pass. Within one scan there is no observable ordering — promotion, execution,
and output folding share one `ScanRecord.clock` — so a strict rule would fail
exactly the case most often wanted. A gap larger than the budget fails, and a
reversed pair reports the ordering violation rather than a budget overrun.

The budget is a real temporal requirement, so state it from what the scenario
needs, not from what the sim currently does. When the real constraint is
unknown, write a generously loose budget and say in a comment that it is an
unvalidated placeholder — an obviously loose number is honest, whereas a
precise-looking `120ms` reads as measured. `observed_gap_ms` is reported on
every evaluation, pass or fail, so budgets can be tightened from measurement.

`PRECEDES` bounds a gap. It does not establish causation, and cannot distinguish
causation from coincidence.

### `CAUSES`

`CAUSES` is attribution, not timing. It takes no budget because it reads no
clock on the pass/fail path — which is what lets it survive the move off
lockstep simulation, where two physical PLCs share no scan boundary but message
identity still travels with the message.

Two rules beyond the grammar:

- **The cause must be a declared comm tag.** Only tag messages record the sender
  and sequence number attribution needs. Plant-routed and strategy-routed
  messages record no sender, so a `CAUSES` naming one can never pass — it is
  rejected at spec load rather than failing at verification time, where it would
  read as a behavior bug.
- **A signal cannot cause itself.**

The claim is read from the receipt recorded where the message was delivered —
sender, sequence, and the value actually delivered — never re-derived from the
trace's merged signal view. A receipt only activates the chain if the delivered
value was truthy: a producer that sends every scan delivers `False` long before
the real event, and binding to the first receipt of any value would attribute
the effect to a message saying nothing happened. Same-scan receipt and action
passes, mirroring `PRECEDES`.

## Two stacked edge concepts

`Plant.routes[].trigger` (`edge` or `level`, for the conveyor) and `when.edge`
are **independent detectors in series**. The route trigger governs whether the
plant emits the sensor value to the PLC at all — `level` re-emits every scan the
sensor is true, `edge` emits only on the sensor's own transition. `when.edge`
then runs on what arrived.

`edge` routing plus `edge: rising` means the trigger sees a single scan of
truth and never sees the signal again; combined with `mode: steady` the output
is one scan wide. This is a real combination, not a mistake — but it is the
combination most often written by accident.

## A complete spec

```yaml spec
System:
  name: doc_example
  plcs:
    - id: plc_a
      role: upstream
    - id: plc_b
      role: downstream

Comm:
  strategy: tag
  tags:
    - name: release_request
      produced_by: plc_a
      consumed_by: [plc_b]

Plant:
  type: conveyor
  config:
    belt_speed_m_per_s: 0.5
    sensor_trigger_threshold_m: 0.1
    actuator_latency_ms: 50.0
  routes:
    - sensor: sensor_a_exit_triggered
      to_plc: plc_a
      as_key: sensor_a_exit
      trigger: level
    - sensor: part_at_b
      to_plc: plc_b
      as_key: part_at_b
      trigger: level
  actuators:
    - from_plc: plc_b
      key: belt_b_enable
      as: belt_b_enable_signal

Behavior:
  plc_a:
    triggers:
      - id: request_on_stable_exit
        when:
          signal: sensor_a_exit
          edge: rising
          debounce_ms: 20
        emit:
          tag: release_request
          mode: latched
  plc_b:
    triggers:
      - id: pulse_belt_on_request
        when:
          signal: release_request
          edge: rising
        emit:
          output: belt_b_enable
          mode: pulse
          duration_ms: 100

Assertions:
  - "EVENTUALLY(part_at_b, within: 500ms)"
  - "CAUSES(release_request, belt_b_enable)"
```

## Fence conventions in this file

The binding test reads fenced blocks by info string:

- ` ```yaml spec ` — a complete spec. The test writes it to a temp file and
  asserts `validate_spec_file` returns no issues.
- ` ```yaml fragment ` — an illustrative excerpt that will not validate alone.
  Not collected.

A bare ` ```yaml ` fence is a test failure, so a new example cannot escape the
gate by omission.

## Adding a spec to `specs/`

`tools/regenerate_expectations.py` globs `specs/*.yaml`, so a spec dropped there
generates `specs/expectations/<System.name>.expected.json` on the next run, and
CI runs that regeneration followed by `git diff --exit-code specs/expectations/`.
A new spec must therefore ship its committed expectations artifact, and since
the artifact records certified verdicts, the spec must be **sim-certified** —
every assertion in it actually passing under simulation, not merely validating.
