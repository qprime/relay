# Checkpoint Architecture for Compiled Systems

**Status:** Internal draft v5 | **Author:** Stephen Quinlan | **As-of:** 2026-05-14
**Audience:** self (working notes; condense for external use later)

---

## Thesis

A useful class of systems shares a common architecture: a small declarative input language, a chain of deterministic compilation stages connecting that input to a final artifact or observable behavior, and a small set of designated **checkpoints** at which a user inspects the system's work at a level of abstraction they can actually judge. Between checkpoints, the system is mechanism — unreviewable line by line, deliberately so. The architecture's contribution is to make sure that *when the user confirms at every checkpoint, the system as a whole is correct.*

Two checkpoint topologies recur in real systems:

- **Fan-out from a certified IR.** A single trusted intermediate representation produces multiple inspectable views (visualizations, previews, the final artifact). The user reviews whichever view best surfaces correctness at their level of abstraction; downstream artifacts derived from the same IR inherit the confirmation.
- **Series of execution stages.** Each stage runs the system in a progressively more realistic environment, produces behavioral evidence (a trace), and that evidence becomes the contract the next stage must satisfy. The chain ends at the physical demonstration.

The shape a system takes is not stylistic. It is forced by whether the artifact's correctness is **certifiable at the IR** (geometry, structured data, deterministic translations) or **only certifiable through execution** (behavior in environments with timing, pacing, or I/O the design layer cannot fully model). Both shapes share the same six middle-layer disciplines; what differs is how checkpoints compose into the system.

This document develops the framework, grounds it in two case studies (`mill_ui`, a CAM system; `relay`, a PLC simulation framework), and argues that the *checkpoint topology* is the principal architectural decision in any compiled system where the user is responsible for the input and the demonstration but not for the mechanism that connects them.

---

## 1. The checkpoint pattern

### 1.1 What a checkpoint is

A compiled system runs input through a chain of transformations to produce an output. Internal stages — parsers, intermediate representations, planners, generators, runtimes — are mechanism. The user doesn't read them line by line, by design.

A **checkpoint** is a stage where the system's work-so-far is deliberately rendered into a form the user can inspect and judge against intent:

> A checkpoint is a representation of the system's work-so-far, presented in a form the user can judge against intent without expert review of the layers above.

Three properties make a checkpoint useful:

- **Native form.** The representation surfaces what matters at this stage in a way the human can read directly. YAML for design review, an SVG blueprint for geometric review, a scan-by-scan trace for behavioral review, a physical part for physical review.
- **Density of judgment.** Per unit of inspection effort, the checkpoint reveals the most about correctness. A 50-line PML document plus a one-screen SVG conveys more about a design than 800 lines of equivalent G-code.
- **Cost asymmetry.** Catching a problem at this checkpoint is meaningfully cheaper than catching it at a later one. The SVG review catches problems before material is cut. The simulation trace review catches problems before deployment.

If a stage doesn't have those properties, it's not a checkpoint. Generated G-code, generated ST function blocks, internal IR nodes — these are *intermediate artifacts*, not checkpoints. The architecture keeps them mechanical; the user isn't asked to inspect them because they fail the density-of-judgment test.

### 1.2 What the user does at a checkpoint

At every checkpoint the user is doing one of two things:

- **Confirming a structured restatement of intent.** "This input document is what I asked for." This is a *forward* confirmation: the structured form is accepted as the basis for downstream work.
- **Judging a demonstration against intent.** "This visualization / trace / physical artifact realizes what I asked for." This is a *backward* judgment: the system's output is accepted as faithful to intent.

It is worth separating two modes the term "user" can refer to, because they do different work:

- **Builder mode.** Someone designing the system: choosing the topology, drafting invariants, snapshotting recipe goldens, deciding which checkpoints exist and how they render. This work happens during system construction. It is rare, deliberate, and high-leverage. It sets the conditions under which any later checkpoint confirmation is meaningful.
- **Operator mode.** Someone using the built system: supplying input, reviewing checkpoints, judging the demonstration. The compiled system is treated like any other deterministic tool — feed it valid input, get valid output, judge the output against intent.

The two modes occur at different times and have different responsibilities. The architectural work — invariants, goldens, validation topology — is builder-mode work and lives in the system's source. An operator running an established system does not redesign invariants; they consume the architecture the builder produced. When this document says "the user" without qualifier, it means the operator. Where builder-mode is meant, it is named explicitly.

### 1.3 Where checkpoints sit relative to authorship

The input to a compiled system can be authored by any means — typed by hand, emitted by a GUI, produced by a code generator, generated from natural language by a tool, copied from a template. The architecture is indifferent to the authoring method. The checkpoint structure runs the same checks regardless: schema validation, IR construction, IR-level validation, and the human's confirmation at checkpoint 2.

This indifference is structural. It is what allows the same architecture to support beginner-friendly natural-language frontends, expert-friendly hand-authoring, and machine-generated input from external tools — all without changing the trust topology of the system. The compiled system is used like any other deterministic compiled system: feed it valid input, get valid output, judge the output against intent.

---

## 2. Two checkpoint topologies

### 2.1 Fan-out from a certified IR

In some domains, the artifact's correctness is certifiable at the level of a structured intermediate representation. Once the IR is right, the final artifact is a deterministic function of it — and any number of inspectable views can be derived from the same IR. The user reviews whichever view they can most reliably judge at; the final artifact inherits the confirmation.

```
input → DSL → IR ─┬─→ visualization (SVG, render, preview)
                  │
                  ├─→ blueprint / report (PDF, dimensions, etc.)
                  │
                  ├─→ secondary outputs (lifted DSL, JSON, etc.)
                  │
                  └─→ final artifact (G-code, deliverable)
                                         ↓
                                physical demonstration
                                (the part, the build)
```

Properties of fan-out:

- **One certifying point.** The IR is where validation lives, where invariants apply, where regression goldens get snapshotted. Confirm the IR is right (directly or via one of its views) and downstream artifacts are correct by construction.
- **Multiple views, one truth.** The visualization, the blueprint, the lifted DSL, and the final artifact are all derived from the same IR. They are not contracts for each other; they are alternative renderings of one thing.
- **Single terminal demonstration.** Because the IR is certifiable, the final physical demonstration confirms the whole stack — there's no behavioral variability downstream that the IR couldn't account for.

This topology works when *physical realization of the artifact is a deterministic function of its specification*. Geometry has this property. So do many configuration-driven systems where the runtime is deterministic given the configuration.

### 2.2 Series of execution stages

In other domains, no IR is sufficient to certify the final output, because the final output is *behavior* in execution environments the design layer cannot fully model. Wall-clock timing, inter-process boundaries, real I/O, fieldbus latency — these are sources of variability that emerge at execution and cannot be reduced to a static description.

The architectural response is to make execution itself the validation surface. Each stage in a chain runs the system in a progressively less-controlled environment, produces behavioral evidence (a trace), and that evidence becomes the contract the next stage must satisfy. The contract — typically a set of assertions over behavior — does not change as you walk the chain; only the environment in which it must hold becomes more concrete.

```
input → DSL → stage 1: oracle execution (deterministic, in-process)
                       ↓
                 trace + certified assertions → contract artifact
                       ↓
              stage 2: execution with one new class of complexity
                       ↓
                 trace evaluated against same contract
                       ↓
              stage 3: execution with another new class of complexity
                       ↓
                 trace evaluated against same contract
                       ⋮
              final stage: physical hardware
```

Properties of series:

- **Each stage certifies the next.** Stage N produces evidence that becomes the contract for stage N+1. Approval at any stage is conditional on the contract being right; expanding what's certified happens stage by stage.
- **Failures localize.** When stage N satisfies the contract and stage N+1 does not, the bug lives in whatever N+1 newly introduced. This is the entire point of staging.
- **No single "final demonstration" that validates the whole stack.** Even the real-hardware run is judged against the contract; the contract was certified by the oracle stage; correctness is a property of the chain holding together, not of any one stage's outcome.
- **The stage that produces the contract must be fully deterministic.** This is the load-bearing assumption: the chain certifies something only when its origin is a trusted, repeatable execution. Any non-deterministic step inside the certifying stage breaks the property that "downstream stage satisfies the contract" implies "downstream stage is correct" — instead, it would merely imply "downstream stage is consistent with the upstream stage," which is a closed loop with no anchor.

This topology works when *the artifact's behavior cannot be reduced to its description*. Control systems, distributed systems, anything where deployment environments introduce variability the design environment cannot model.

### 2.3 Both shapes share the same middle

The difference between the two topologies is downstream of the structured input. Both shapes require the same six middle-layer disciplines:

1. **A small declarative input language** the user can review (and can hand-author if they choose to).
2. **Explicit IRs** as inspectable evidence of each compiler stage.
3. **IR-level validation** that catches problems against the semantic description.
4. **Reverse paths** so the system describes its own outputs in the user's vocabulary.
5. **Validation discipline as a first-class artifact** (goldens, invariants, recipe regressions).
6. **A refusal to locate the contract in the middle.** Tests don't define correctness; they enforce contracts defined at checkpoints.

What differs is the *checkpoint topology* layered over that middle. The same six moves are what make a compiled system trustworthy; the topology is what makes the system safe to *deploy*.

### 2.4 Real systems mix the shapes

A system can have both. A principally fan-out system can incorporate a downstream series stage (e.g., probing a cut part and comparing measurements to the model). A principally series system fans out internally at each stage (the same trace supports assertion evaluation, scan-by-scan inspection, and the expectations artifact). The taxonomy describes *primary shape*; mixing is normal.

---

## 3. Case study: mill_ui

`mill_ui` is a CAM system that turns declarative panel layouts into G-code for CNC routers. Its checkpoint topology is fan-out from the RemovalIntent IR.

### 3.1 Checkpoint sequence

| # | Checkpoint | Native form | What the user judges |
|---|------------|-------------|----------------------|
| 1 | Intent | Mental model, sketch, requirements doc | What I want to build |
| 2 | PML review | YAML document | The structured interpretation of intent — geometry, features, layout |
| 3 | SVG blueprint review | 2D rendered visualization | Geometry and layout — does this match what I asked for? |
| 4 | G-code viewer / preview review (optional) | Path animation in a viewer | Toolpath sanity — feeds, retracts, plunge depths look right |
| 5 | Real cut | Physical part on the CNC | The part actually fits, mates, looks right |

Checkpoints 2, 3, and 4 all derive from the same RemovalIntent IR. The PML lifts back to LayoutAST and forward into the IR; the SVG is rendered from the IR; the G-code is generated from the IR. Confirming any of them is a confirmation of the IR itself — the user picks the level of abstraction they can most reliably judge at.

In practice, checkpoint 2 (PML) catches "structured restatement diverged from intent" failures cheaply. Checkpoint 3 (SVG) catches "the design looks right in text but wrong as geometry" failures. Checkpoint 4 (G-code preview) catches "the toolpaths take an awkward route or violate a machine constraint" failures. Checkpoint 5 (real cut) catches everything left over and is the demonstration that the architecture worked end-to-end.

### 3.2 PML is hand-authorable

PML is designed to be written by hand. Field names are semantic; the schema is small; layout managers (`Frame`, `Grid`, `Split`, `Inset`) let the author describe structure rather than coordinates. A 50-line PML document is a reasonable description of a non-trivial part.

This is a structural property, not a feature. It is what makes the architecture's indifference to authoring method honest rather than aspirational. The same PML can come from a hand-typed editor, a GUI tool, a code generator, or a natural-language frontend — the compiler's behavior at checkpoint 2 is identical. The system is operated like any other deterministic tool that accepts a declarative input file.

If PML were only ergonomic under tool-assisted authorship, the indifference claim would be marketing. The test is whether an expert prefers writing PML directly to using whatever frontend exists — not whether they tolerate it. `mill_ui` passes that test in practice; recipes in `docs/recipes/` are hand-authored and reviewed as such.

### 3.3 Why fan-out works here

The artifact is geometry. Once the RemovalIntent IR is right — and once the planner and post-processor are deterministic and validated against the recipe goldens — the physical part is a deterministic function of the IR. The SVG and the G-code are alternative renderings of the same truth. Confirming the SVG is, materially, confirming the G-code.

This is what allows `mill_ui` to ship a regression suite of 70+ recipes with golden metrics as its primary defense. The goldens lock down the IR-to-artifact translation. Drift surfaces as a structured diff against a named recipe. The user is not in the loop on every recipe; they were in the loop *once*, when the recipe was first snapshotted, and the architecture preserves that confirmation across all future runs.

### 3.4 The middle's job

Between checkpoints, the architecture enforces honesty:

- PML schema validation rejects malformed input before any downstream stage runs.
- LayoutAST resolution expands compositional containers into a flat geometric tree.
- RemovalIntent IR validation runs overlap, depth feasibility, and toolability checks against the semantic description. Errors here surface in the user's language, not as G-code-level cryptics.
- The planner is deterministic against the machine configuration.
- The post-processor emits both G-code and the SVG blueprint from the same IR.
- The recipe regression suite confirms that, for each canonical PML, the IR / planner output / G-code / SVG match committed goldens.

None of these layers ask the user for confirmation. The user confirmed once, at checkpoint 2 or 3, and the architecture preserves that confirmation down to the cut.

---

## 4. Case study: relay

`relay` is a PLC simulation framework. Its checkpoint topology is a series of execution stages, each producing the contract the next stage must satisfy.

### 4.1 Checkpoint sequence

| # | Checkpoint | Native form | What the user judges |
|---|------------|-------------|----------------------|
| 1 | Intent | Requirements document, mental model | What control behavior I want |
| 2 | Task spec review | YAML document | The structured interpretation of intent: PLC topology, plant configuration, comm strategy, structured trigger semantics that define mechanism, and the assertions that define success |
| 3 | Python simulation trace review | Scan-by-scan trace + assertion verdicts | Behavior in the deterministic oracle environment |
| 4 | C++ host trace review (planned) | Same trace shape, wall-clock paced | Behavior survives wall-clock pacing |
| 5 | Socket-plant integration trace review (aspirational) | Same trace shape, inter-process | Behavior survives the process boundary |
| 6 | Real hardware (aspirational) | Live system observation | The control strategy works against physical I/O |

Checkpoint 2 is load-bearing. The user is confirming not only what behavior is desired (the assertions) but also the structured triggers that define *how* the system should respond — what events cause what signals to fire. The structured-trigger semantics are part of what the user confirms; nothing south of checkpoint 2 should require re-interpretation.

A critical fact, the one that distinguishes this from `mill_ui`: **the user cannot judge a control behavior the way they can judge a cut part.** Even at checkpoint 6 — real hardware — observing the system run is not by itself sufficient to confirm correctness. The user observes the system; the architecture asserts (via the same `EVENTUALLY` and `PRECEDES` contracts certified at checkpoint 3) that the observed behavior met the contract. Physical demonstration in `relay` *is* validation by execution against an assertion contract. There is no equivalent of "look at the part and see it's right."

This is why no single representation in `relay` can play the role the SVG plays in `mill_ui`. The task spec is dense but cannot show whether the scenario will actually behave as intended; the generated ST is dense and unreviewable; the trace is the *only* representation in which behavior can be judged, and it can only exist after execution.

### 4.2 The task spec is hand-authorable

The task spec is designed to be written by hand. Every field is semantically named. Every trigger is structured: events, conditions, and resulting signals are named explicitly, not described in prose. Assertions are in a small, formal grammar (`EVENTUALLY`, `PRECEDES`) the user can read directly.

This is what makes checkpoint 2 meaningful. The user is reading a complete, deterministic specification of the scenario — not a hint that downstream stages will interpret. Two consequences:

- The architecture is genuinely indifferent to how the task spec was produced. Hand-typed, GUI-emitted, code-generated, conversation-derived — the compiler runs the same checks and produces the same downstream artifacts.
- Determinism is anchored at checkpoint 2. Any ambiguity left in the task spec would have to be resolved somewhere downstream, and resolving it would require a non-deterministic interpretation step. The hand-authorability requirement and the determinism requirement reinforce each other: a spec that is fully unambiguous can be hand-written, and a spec that can be hand-written has nothing left to interpret.

The honest test of this property is whether a control engineer prefers writing the task spec directly to using whatever frontend exists for it. If they merely tolerate it, the schema is letting tool-assisted authorship paper over ambiguity; if they prefer it, the schema is honest. `relay`'s schema is being refined toward this standard.

### 4.3 Why series works here

Behavior cannot be reduced to its description. The task spec says what should happen; whether what should happen *does* happen depends on the runtime, the plant model, the comm bus, the scan loop semantics — all of which are mechanism the user cannot fully review line by line.

The Python simulator is the first stage where behavior actually exists. It runs the generated ST against an injected `SimClock` and an in-process plant physics model, produces a deterministic trace, and the verifier evaluates the assertions against that trace. The trace is the artifact the user can inspect; the assertions are the contract; the simulator certifies that the contract holds in the oracle environment.

That certification becomes the contract for the next stage. The C++ host runs the same generated ST under wall-clock pacing against a stub plant, produces its own trace, and the same verifier re-evaluates the same assertions against that trace. If they hold, the host has satisfied the contract; if they don't, the bug is in whatever the host newly introduced (pacing, coroutine scheduling, the C++ interpreter). Failures localize.

Each subsequent stage adds one new class of complexity (socket plant: inter-process boundary; real hardware: physical I/O and fieldbus timing) and is judged by the same contract.

### 4.4 The load-bearing premise: deterministic compilation from IR onward

Section 4.3's argument has a premise that deserves explicit naming. *Physical demonstration is validation by execution against an assertion contract* only holds if the assertion contract and the executed mechanism are produced by independent, deterministic processes. If the contract and the mechanism were derived from a single non-deterministic stage, then "trace satisfies the contract" reduces to "mechanism agrees with specification," and certification collapses.

The architectural response is to constrain where non-determinism is allowed to sit. The structured input — the task spec, including the assertions — is reviewed by the user at checkpoint 2. Everything downstream of the task spec is deterministic compilation: a closed grammar, validated transformations, no non-deterministic passes. The same task spec must produce the same generated ST, the same trace, the same verdict, every run.

In `relay`, this is operationalized by five invariants:

- `scan_phase_isolation` — the per-scan phase order is fixed and FB execution is pure.
- `simclock_only_time_source` — every time value in the execution path is injected, never read from the wall.
- `comm_bus_only_inter_plc_channel` — no side channels between PLCs.
- `verification_path_purity` — the assertion evaluator has a closed import set; no non-deterministic dependencies.
- `pipeline_direction_imports` — imports flow strictly forward through the pipeline.

Each invariant exists because violating it would silently degrade the trace's value as evidence. Determinism is a project-wide property south of checkpoint 2, not a feature of one subsystem. Without it, the series topology certifies nothing.

### 4.5 The middle's job

Between checkpoints, `relay` enforces honesty through documented invariants rather than recipe goldens. The invariants are what make checkpoint 3 (the trace) a usable contract for checkpoint 4 (the host trace).

The reason invariants suit this domain better than goldens: each downstream stage runs in a different environment. The C++ host's trace will not be bit-identical to the Python sim's trace — that's fine; the contract is the assertions, not the trace bytes. Goldens of the trace would over-specify, locking down properties that aren't part of the contract. Invariants generalize across environments in a way goldens don't. "ST execution is pure" applies in Python and in C++ alike. "SimClock is the only time source" applies in any execution environment that wants to be evidence-producing.

One distinction this rules out and one it doesn't. What is rejected is the golden as a *cross-environment equality oracle* — asserting the C++ trace matches the Python trace byte-for-byte, which would fail on differences that carry no meaning and certify nothing when it passed. What remains legitimate is the golden as a *wire-format reference*: `tests/golden/conveyor_trace.jsonl` exists so a C++ implementer can diff their emitter's output against a known-good encoding of the shared format, converting format drift into a one-line diff rather than an assertion mismatch three layers downstream. It pins how a record is spelled, never which records a given environment produces. The first use makes the trace bytes the contract; the second makes them a shared alphabet the contract is written in.

---

## 5. What's shared

Both projects realize the same six middle-layer disciplines:

| Move | mill_ui | relay |
|------|---------|-------|
| Small declarative DSL | PML (YAML) | Task spec (YAML) |
| Explicit IRs | LayoutAST, RemovalIntent IR | Task spec, ST blocks, trace log |
| IR-level validation | Overlap, toolability, depth feasibility | Trace-based assertions |
| Reverse path | `pml/lifter.py` | Expectations artifact (planned), trace inspection |
| Validation discipline | 70+ recipes with golden metrics | Documented invariants + closed-import verifier |
| Contract never in middle | Goldens are the contract; tests enforce them | Invariants are the contract; tests enforce them |

Both partition the user's responsibilities the same way:

- The user authors: **intent**, **architecture**, and **checkpoint confirmations**.
- The system supplies: **everything else** — DSL parsing, IR transformations, generated artifacts, validation code, tests.

The user's trust budget is spent at checkpoints (forward-confirming intent, judging demonstrations) and in the architecture (the disciplines that keep the middle honest). It is not spent on line-by-line review of internal stages, which is the point of having a compiled system in the first place.

---

## 6. What differs

The projects diverge in checkpoint topology, and the divergence is forced by what they produce:

| Aspect | mill_ui | relay |
|--------|---------|-------|
| Artifact | Geometry (G-code, blueprints) | Behavior (control logic running on hardware) |
| Realization | Deterministic function of IR | Function of execution environment |
| Topology | Fan-out from certified IR | Series of execution stages |
| Number of inspection forms | Many (PML, SVG, G-code preview, part) | Few (task spec, traces — all behavioral) |
| Final demonstration | Direct visual judgment of physical part | Assertion evaluation against observed behavior |
| Primary middle-layer discipline | Recipe goldens | Documented invariants |

The general principle:

> **The checkpoint topology is determined by whether the artifact's correctness is certifiable at an IR (fan-out) or only through execution (series).**
>
> Fan-out systems produce artifacts whose physical realization is a deterministic function of their specification. One certifying point, many inspectable views, one terminal demonstration.
>
> Series systems produce artifacts whose realization involves environmental variability the design cannot model. Multiple certifying points, each producing the contract for the next, no single point that validates the whole stack. The chain must be anchored at a deterministic origin or it certifies nothing.

Neither topology is a deficiency. They are honest expressions of the same architectural school applied to different artifact types. A system that ought to be series but is built as fan-out will be brittle in deployment because the IR's certification claims more than it can deliver. A system that ought to be fan-out but is built as series will be over-engineered, paying for execution-stage checkpoints it doesn't need.

The first design question for a new system in this lineage is therefore: *which topology does my artifact require?* The answer is not stylistic; it follows from whether the artifact's correctness can be reduced to a specification or whether it can only be observed in execution.

---

## 7. Implications

### For practitioners

When designing a compiled system, the design questions in order:

1. **What is the user's intent?** The shape of the request that begins each session.
2. **What is the final demonstration?** A physical artifact, a behavioral observation, an integration test against external systems. Be specific about what the user *actually* judges.
3. **Is the artifact's realization deterministic given its specification?** This decides topology. If yes, fan-out. If no, series.
4. **What are the checkpoints between intent and demonstration?** Identify the points where rendering the work-so-far into an inspectable native form repays the cost.
5. **What lies between checkpoints?** IRs, validations, compiler stages, generated artifacts, tests. The architecture is judged on whether it makes the path from input to demonstration trustworthy.
6. **What primary middle-layer discipline?** Goldens (for fan-out — deterministic translations need diff-able evidence) or invariants (for series — execution stages need cross-environment-portable rules) or both.
7. **Where is non-determinism allowed to sit?** Upstream of the IR — yes, with checkpoint confirmation. Downstream of the IR — no, or the architecture stops certifying anything. The structured input is reviewed; everything south of it must be repeatable.

The investment is substantial. The payoff is a system that can be operated like any other deterministic compiled system: feed it valid input, get valid output, judge the output against intent.

### For evaluators

The signal that a compiled system is trustworthy is the *number, design quality, and inspectability of its checkpoints* — combined with the *deterministic discipline* applied to everything downstream of the user-reviewed input. A system with many small, well-rendered checkpoints and a strictly deterministic middle is auditable. A system whose only checkpoint is "look at the final output" is fragile in proportion to how opaque the output is.

### For this body of work

`mill_ui` and `relay` are two instances of the same architectural school, in different checkpoint topologies, addressing different artifact types. A useful next step is to factor the pattern explicitly — name it, document it independently of either project, treat the next project as a third instance and surface its topology as a first-class design decision.

The pattern in tightest form: *the user authors intent and judges checkpoints; the architecture decides which checkpoints exist, what they render, and what disciplines keep the gaps between them honest.*

---

## 8. Open ends

Items to think harder about before externalizing:

- **What makes a checkpoint design good or bad?** Native form, density of judgment, cost asymmetry are necessary but probably not sufficient. The space of good checkpoint design deserves explicit characterization — perhaps a typology of checkpoint failure modes (too dense to review, too sparse to be useful, mis-rendered, redundant with a later one).
- **Rubber-stamp confirmations.** A user can approve a checkpoint without genuine review, especially under time pressure. The architecture can't fully prevent this. Native form and density of judgment are necessary but insufficient. Worth more development; the literature on review-interface design has partial answers.
- **Series of how many?** A series chain of four stages, or three, or five, is partly determined by what classes of complexity exist between design and deployment in the target domain, partly by engineering judgment about which boundaries deserve their own checkpoint. A principled account would help.
- **Hybrid topologies.** A principally fan-out system can incorporate a downstream series stage; a principally series system can fan out internally at each stage. The taxonomy should explicitly accommodate hybrids rather than treat them as exceptions.
- **The "small enough to review" claim under nontrivial cases.** Both DSLs are small for trivial examples. Real-world cases produce documents of nontrivial size. Where does checkpoint review stop being tractable? What architectural moves extend it (composition, modular review, derived-property summaries presented alongside)?
- **Mis-confirmation recovery.** A user can mis-approve a checkpoint. The architecture cannot fully protect against this; it can only make it cheap to add a new check once such a failure is observed. The cost-of-recovery story deserves more development.
- **Goldens vs. invariants — principled choice or temperament?** This draft argues the topology forces the choice (fan-out wants goldens; series wants invariants). True at first approximation. Real systems will need both, and the boundary between when each is the right primary tool deserves sharper treatment.
- **Drafting vs. accepting.** Invariants and recipe goldens are typically drafted somewhere — by a contributor, by a tool, by another system — and the user accepts them as architectural. The drafting-vs-accepting distinction is load-bearing but soft. What does it mean to "accept" an invariant, mechanically? An external draft should make this explicit.

---

## 9. Externalization notes

For a public whitepaper:

- Lead with the checkpoint pattern (Section 1) and the two topologies (Section 2). Skip the long thesis paragraph; let the framework introduce itself by example.
- Compress Section 5 (what's shared) — the table is necessary, the prose around it is less so.
- Section 6 (what differs) is the load-bearing intellectual move. Expand the principle into its own section if space allows.
- Add an abstract — three sentences.
- Cite. The IR-based compiler pattern has a deep literature (model-driven engineering, classical compiler construction, formal methods). The framework here sits inside that tradition and should be honest about its inheritances. The contribution is the topology distinction and its tie to artifact type, not the compiler pattern itself.
- The novelty claim is not "we invented compiled systems." It is *"the checkpoint topology — fan-out vs. series — is the principal architectural decision in a compiled system that has to be operated and not just inspected, and the topology choice is forced by whether correctness is certifiable at the IR or only through execution."*
- Title candidates: *"Checkpoint Architecture for Compiled Systems"* (current); *"Two Topologies of Validated Compilation"*; *"Where the Architecture Puts the User."*
- Find two readers: one who has built a system in this style, one who has built neither. The "neither" reader is the gut check on whether the framework lands cold.
