---
description: Run the full post-implementation close-out workflow — verification, summary, and commit. Only use when the user explicitly asks.
---

# /close-out — Implementation Close-Out

Close out the implementation for: $ARGUMENTS

## Phase 1: Verification

Run all verification steps. Do not skip any.

1. Full test suite: `uv run pytest`
2. Lint: `uv run ruff check relay/ tests/`

**ALL tests must pass. Zero failures, no exceptions.**

**Context discipline:** Pipe large output to `tail` for summary. Re-run without tail on failure.

## Phase 2: Implementation Summary

Draft summary as GitHub issue comment:

```
## Implementation Summary

<1-2 sentence description>

### Files Modified (<N>)

| File | Change |
|------|--------|
| `path/to/file.py` | Description |

### Design Notes
- Key architectural decisions

### Test Results
<N> passed, zero failures
```

Present to user before posting.

## Phase 3: Commit

1. Stage relevant files (specific, not `git add -A`)
2. Commit with:
   - Subject: imperative mood, `(closes #N)` if closing issue
   - Body: categorized bullet points
   - Trailer: `Co-Authored-By: Claude <model> <noreply@anthropic.com>`
3. Run `git status` to confirm clean state

Do NOT push unless explicitly asked.

## Phase 4: Final Summary

```
Committed as `<hash>` — lint/tests pass (<N> passed).

### What shipped
<2-3 sentence summary>

### Files
- <N> source files modified/created
```
