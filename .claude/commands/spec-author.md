---
description: Author a RELAY task spec YAML from an intent described in conversation. Use when the user wants a new spec written, an existing spec changed, or a validation failure diagnosed. Drives the write → validate → fix → sim-certify loop.
---

# /spec-author

You write task specs. The task spec is the semantic IR everything downstream derives from — generated ST, the simulation, the verifier's verdicts — so a spec that is legal but says the wrong thing produces legal-looking wrong ST and a clean PASS on behavior nobody wanted.

Your job is the part the validator cannot do. `tools/validate_spec.py` rejects malformed specs; nothing rejects a well-formed spec that asserts the wrong claim. That gap is where you earn your keep.

## Read first

- [docs/task_spec_syntax.md](../../docs/task_spec_syntax.md) — the syntax authority. Block structure, trigger IR fields, assertion grammar, and the rules the validator enforces. Do not author from memory; do not restate it back to the user.
- [specs/conveyor_handoff.yaml](../../specs/conveyor_handoff.yaml) — latched handoff across two PLCs.
- [specs/conveyor_pulse_release.yaml](../../specs/conveyor_pulse_release.yaml) — debounced rising edge and a pulsed output.

For `Comm` and `Plant` block fields, read the `validate_config` of the strategy and plant the spec declares. Those blocks are strategy-owned; a spec carries one strategy's idiom, never the union.

## The loop

1. **Restate the intent as observable claims** before writing YAML. "Part reaches B" is a claim. "The handoff works" is not. If the user's intent can't be stated as claims over signals, the intent is underspecified — say so and ask, rather than picking claims for them.
2. **Write the spec.**
3. **`python -m tools.validate_spec <path>`.** Fix every issue. Issues accumulate, so one run usually shows all mechanical problems at once.
4. **`python -m tools.expectations <path>`.** Every assertion must show `"passed": true`. An assertion that validates but fails under simulation means the spec's behavior and its claims disagree — diagnose which one is wrong, and say which. Do not weaken an assertion to make it pass.
5. **Certify.** Once the spec lands in `specs/`, run `python -m tools.regenerate_expectations` and commit the artifact alongside it. CI diffs `specs/expectations/`.

Step 4 is not optional. A spec that has only been validated is a spec nobody has run.

## Critical Rules

The validator catches structural errors. These are the ones it cannot see, because each produces a spec that is entirely well-formed and quietly wrong.

**Assert the signal the claim is about, not the one that is convenient.** `EVENTUALLY(belt_b_enable, ...)` asserts that a PLC decided to run the belt. `EVENTUALLY(part_at_b, ...)` asserts that the part actually arrived. The first passes even if the plant never moves. When a plant sensor and a PLC output both describe "the thing happened," the sensor is the stronger claim and usually the intended one — the output only proves the controller's intent.

**A budget is a requirement, not a measurement.** `within: 500ms` says the system must do this in 500 ms. If nobody knows the real deadline, write a loose one and comment that it is an unvalidated placeholder. A precise-looking `120ms` reads as measured and misleads every later reader. Never copy a budget from `observed_gap_ms` and present it as a requirement — that inverts the direction of the claim, turning "what we require" into "what it happened to do," and it will fail the first time timing shifts legitimately.

**`PRECEDES` is not causation.** It bounds a gap between two first-activations. Two signals that always fire together for unrelated reasons satisfy it forever. When the claim is "B happened *because* A arrived," write `CAUSES` — it is the only form that reads message attribution. Reach for `PRECEDES` when the claim is genuinely about a deadline.

**Match the edge to the physical event.** `rising` fires on a transition; `level` fires continuously while true. A trigger that should act once per part but reads `level` re-fires every scan; with `mode: steady` its output chatters. Ask what the sensor physically does before choosing — and remember `Plant.routes[].trigger` is a second, independent edge detector stacked in front of `when.edge`.

**`debounce_ms` moves the event, it doesn't just filter it.** A debounced `rising` fires when the signal has *already been high* for the window, not at the transition. Any budget written downstream of a debounced trigger must include the debounce window, or it encodes a deadline the system cannot meet for reasons that have nothing to do with the behavior under test.

**`latched` never clears.** There is no reset primitive. If the scenario needs the signal to drop — a cycle that repeats, a fault that clears — `latched` is wrong and the spec will look correct while modeling a one-shot machine.

**Every assertion must be able to fail.** Before finishing, ask of each one: what behavior change would make this fail? If nothing plausible would, it certifies nothing. An `EVENTUALLY` with a budget far beyond any real timing, or one naming a signal that is true in the first scan, is decoration.

## Diagnosing a validation failure

Read the message before changing the spec — the validator's errors name the resolution rule they enforce, and the fix usually follows from the rule rather than from guessing.

- **"does not resolve to a Plant route as_key for this PLC or a Comm tag it consumes"** — the PLC cannot read that signal. A tag a PLC *produces* is not readable by it; route it or consume it.
- **"collides with a Plant route as_key"** — the output would mask the sensor during assertion resolution. Rename the output; do not rename the sensor to dodge it.
- **"one trigger per target"** — two triggers on one PLC write the same target. Merge the conditions into one trigger.
- **Terminal selector error** — an unregistered `Comm.strategy` or `Plant.type` stops validation before anything else runs, so it is the only issue reported. Fix it and re-run to see the rest.

## Don't

- Author a `Plant.type` that is not registered. `conveyor` is the only Python plant today; a new plant model is a framework change, not a spec change.
- Hand-write an expectations artifact. It is generated from a simulation run.
- Weaken or delete an assertion to make the sim pass.
- Restate `docs/task_spec_syntax.md` to the user. Link it.
