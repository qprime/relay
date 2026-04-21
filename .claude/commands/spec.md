---
description: Draft a GitHub issue implementation specification. Use when planning a new feature, refactor, or bug fix that needs a detailed spec before implementation.
---

# /spec — Implementation Specification

Draft a GitHub issue implementation specification for: $ARGUMENTS

## Process

1. **Research first.** Read the relevant source files, invariants, and existing patterns before writing anything. Understand what exists before proposing changes.

2. **Draft the spec** as a GitHub issue body using the section template below. Omit a section if it doesn't apply.

3. **Check for a smaller change.** Before finalizing, ask: could a narrower scope achieve the same goal?

4. **Self-review the draft.** Flag anything that rests on an unverified assumption.

5. **Present the draft** to the user for review before creating the issue.

---

## Title

Start with an action verb. Describe what the change *does*, not what's missing.

## Section Template

### Summary
1-3 sentences. What is being added, changed, or fixed. Actionable and specific.

### Motivation
Why this matters. Concrete pain points. Not hypothetical benefits.

### Existing Architecture
What exists today that this change touches. Reference specific files and line numbers.

### Design
The technical approach:
- **Data flow**: How data moves through layers
- **Code signatures**: Exact dataclass fields, function signatures with type annotations
- **Invariant impact**: Which invariants does this touch?
- **Verification integrity**: Does this keep LLM out of the verification path?
- **ST scope impact**: Does this require extending the interpreter subset?

### Implementation
Phased or numbered steps. Per-file change table when touching 3+ files:

| File | Change |
|------|--------|
| `path/to/file.py` | Description |

### Invariants
For each relevant invariant: ID, name, complies or requires exception.

### Edge Cases
Scenarios worth calling out and expected behavior.

### Testing Strategy
Named test cases:

TestClassName:
    test_case_name — description of what it verifies

### What NOT to do
Anti-patterns and scope boundaries. Each bullet must: prevent a prior failure, be non-obvious, or draw a scope boundary.

### Files to Modify
Master table of every file created or modified.

---

## Quality Checks

- [ ] Every file referenced actually exists (or explicitly marked new)
- [ ] Function signatures match the actual codebase
- [ ] Invariant IDs are real (check `docs/invariants/`)
- [ ] No LLM in the verification path (check Design section)
- [ ] No shared PLC state outside CommBus (check Design section)
