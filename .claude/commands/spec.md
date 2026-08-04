---
layer: global
description: Draft a GitHub issue implementation specification. Use when planning a new feature, refactor, or bug fix that needs a detailed spec before implementation.
---

# /spec — Implementation Specification

Draft a GitHub issue implementation specification for: $ARGUMENTS

## Process

1. **Research first.** Before drafting, read: (a) every file named in the feature request, (b) the implementation files of any named subsystem, (c) the task spec schema in `relay/spec/`, I/O image layout in `relay/runtime/`, and temporal assertion definitions in `relay/verify/`, and (d) existing tests covering those paths. List what you read in a short "Context loaded" block before drafting. Understand what exists before proposing changes.

2. **Draft the spec** using the section template below. Every section is required unless explicitly marked optional. Omit a section only if it genuinely does not apply.

3. **Check for a smaller change.** Before finalizing, ask: could a narrower scope — fewer files, fewer moving parts, less ceremony — achieve the same goal? If yes, redraft around that. Spec size should match change size. This is about scope, not about removing structure that serves invariants, type safety, or tests.

4. **Self-review and resolve.** Read the draft back as if you hadn't written it. For every point of doubt — an unverified assumption, an unchecked signature or line number, a claim that sounds confident but isn't grounded in something you actually read — resolve it yourself: read the file, verify the fact, fix the draft. Repeat until every doubt is either resolved or genuinely requires a decision only the user can make (a tradeoff, a scope call, a preference). Surface only those with the draft. A doubt you could resolve by reading code is work to do, not a caveat to report — if nothing needs a user decision, present the draft with no review commentary.

5. **Present the draft** to the user for review before creating the issue.

6. **Trace the pipeline path.** Identify which stages of the relay pipeline (spec parsing → ST generation → scan-cycle simulation → trace verification) the change touches. If the change crosses a stage boundary, verify the contract on both sides before drafting the design.

---

## Title

Start with an action verb. Describe what the change *does*, not what's missing or broken.

- **Good:** "Add drift detection for baseline version comparisons"
- **Bad:** "Projects don't know when their baseline is stale"

## Section Template

### Summary
1-3 sentences. What is being added, changed, or fixed. Actionable and specific.

### Motivation
Why this matters. Concrete pain points — user-facing or developer-facing. Not hypothetical benefits.

### Existing Architecture
What exists today that this change touches. Reference specific files and line numbers. Include function signatures, data flow, and relevant patterns. This section grounds the implementation in reality — do not skip it.

### Design
The technical approach:
- **Data flow**: How data moves through the system. Use an ASCII diagram only if the shape isn't obvious from prose.
- **Code signatures**: Exact dataclass fields, function signatures with type annotations
- **Invariant impact**: Which invariants does this touch? Note here if any are bent; full compliance statement goes in the Invariants section below.
- **Layer touched**: state whether the change is in parsing/AST, in the IR, or in codegen. Changes crossing multiple layers must name each layer's change separately.

- **I/O image impact**: if the change touches `relay/runtime/` or `relay/plant/`, state whether the I/O image layout (field names, types, scan-boundary semantics) changes. A layout change is a coupled-surface change and must be reflected on both sides of the runtime↔plant boundary.
- **SimClock / scan-cycle impact**: state whether the change alters scan-cycle timing, scheduling, or SimClock semantics. If it does, explain how determinism is preserved.
- **Temporal contract impact**: if the change affects observable behavior, state which EVENTUALLY / PRECEDES contracts in `relay/verify/` are affected or need to be added.

### Constraint Interactions
How this feature interacts with existing features. For each relevant interaction:
- Is it compatible, mutually exclusive, or conditionally compatible?
- What validation enforces the constraint?

*Optional — omit only if the change is truly isolated (rare).*

### Implementation
Phased or numbered steps. For each step:
- Which file(s) change
- What specifically changes (field additions, new functions, modified logic)

Use a per-file change table when touching 3+ files:

| File | Change |
|------|--------|
| `path/to/file` | Description of change |

### Invariants
Which invariant files apply to this change. For each:
- Invariant ID and name
- Whether this change complies or requires a documented exception

### Edge Cases
Scenarios worth calling out and the expected behavior. Cover what's actually at risk for this change — missing/None inputs where relevant, adjacent work having or not having landed, analysis surfacing something unexpected, partial or conflicting state.

### Testing Strategy
Named test cases with expected behavior:

```
TestClassName:
    test_case_name — description of what it verifies
```

Include at least one test whose failure would catch a plausible wrong implementation — not just one that passes when the code is correct.

- **Purity check**: any generator change needs a test confirming same input produces same output across repeated calls.
- **IR snapshot**: changes that affect IR shape should include a test that pins the IR for a reference input.

- **Scan-cycle determinism**: drive timing-sensitive tests at fixed scan counts via SimClock — never wall-clock sleeps or `time.sleep`. A non-deterministic test is a bug.
- **Temporal assertion coverage**: assert behavioral correctness as EVENTUALLY / PRECEDES contracts over trace logs, not just final-state snapshots.
- **Golden ST diffing**: when a generated-ST test fails, include a golden-output comparison test so regressions are caught by diff, not manual inspection.

### What NOT to do
Anti-patterns and scope boundaries that aren't obvious from the positive rules above. Each bullet must earn its place by meeting at least one of:
- Prevents a failure mode that actually happened in a prior issue/review
- Non-obvious from the Design / Implementation sections (a reader would not infer it)
- Draws a scope boundary against adjacent work (other open issues, sibling subsystems)

If a bullet just restates a rule already given positively, cut it. Omit the whole section if nothing meets the bar.

### Files to Modify
Master table of every file that will be created or modified.

### Dependencies *(optional)*
Related issues, prerequisites, or things this supersedes.

---

## Quality Checks

Before presenting the draft, verify:

- [ ] Every file referenced actually exists (or is explicitly marked as new)
- [ ] Line numbers are current (not stale)
- [ ] Function signatures match the actual codebase
- [ ] Invariant IDs are real
- [ ] No section is vague hand-waving
- [ ] Every "What NOT to do" bullet meets the bar; no bullet restates a positive rule from Design/Implementation
- [ ] Test cases have names, not just descriptions
- [ ] I/O image field names and types match on both sides of the `relay/runtime/` ↔ `relay/plant/` boundary
- [ ] No `time.sleep` or wall-clock timing in scan-cycle test paths — all timing flows through SimClock
- [ ] Every behavioral change is covered by at least one EVENTUALLY or PRECEDES assertion
- [ ] Generated ST is never hand-edited — fixes target `relay/generator/` or `relay/spec/`

Run these checks silently and fix what fails. Mention a check only if it fails and cannot be fixed without a user decision.
