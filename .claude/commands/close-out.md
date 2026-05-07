---
layer: global
description: Run the full post-implementation close-out workflow — verification, summary, and commit. Only use when the user explicitly asks — e.g. "close it out", "wrap it up", "/close-out". Never auto-trigger after implementation.
---

# /close-out — Implementation Close-Out

Close out the implementation for: $ARGUMENTS

This skill runs the full verification, summary, and commit cycle after an implementation is complete. Follow every phase in order.

---

## Phase 1: Verification

**Context discipline:** Pipe large output to `tail` for summary. Re-run without tail on failure.

Detect what exists, then run exactly what's configured.

1. **Tests:** If `tests/` exists or `pyproject.toml` declares pytest config, run `python -m pytest tests/ -x -q 2>&1 | tail -5`. If neither is present, report "no test suite detected" and proceed.
2. **Lint:** If `ruff.toml` or `[tool.ruff]` in `pyproject.toml`, run `ruff check . 2>&1 | tail -3 && ruff format --check . 2>&1 | tail -3`. If neither, report "no ruff config detected".
3. **Type check:** If `pyrightconfig.json` or `[tool.pyright]` in `pyproject.toml`, run `pyright 2>&1 | tail -5`. Skip if neither.

List each check as `RAN`, `SKIPPED (reason)`, or `FAILED` in the Phase 4 summary.

**Every check that ran must pass. Zero failures, zero errors, no exceptions.** Do not classify any failure as "pre-existing" — if it fails, it blocks the commit.

---

## Phase 2: Implementation Summary

Draft an implementation summary:

```
## Implementation Summary

<1-2 sentence description of what shipped.>

### Files Modified (<N>)

| File | Change |
|------|--------|
| `path/to/file` | Description of change |

### Files Created (<N>) *(if any)*

| File | Purpose |
|------|---------|
| `path/to/file` | Purpose |

### Design Notes
- Key architectural decisions or non-obvious choices

### Test Results
<N> passed, zero failures
```

Present to user before posting.

---

## Phase 3: Commit

1. Run `git status` first. Stage only files modified during this implementation session, one `git add <path>` per file. Never `git add -A` or `git add .`. If `git status` shows modifications you did not make this session, ask the user before staging them.
2. Commit with:
   - Subject: imperative mood, `(closes #N)` if closing an issue
   - Body: categorized bullet points
   - Trailer: `Co-Authored-By: Claude <model> <noreply@anthropic.com>` (substitute current model name, e.g. `Claude Opus 4.7 (1M context)`)
3. Run `git status` to confirm clean state

Do NOT push unless explicitly asked.

---

## Phase 4: Final Summary

```
Committed as `<hash>` — all checks pass, <N> tests pass.

### What shipped
<2-3 sentence summary>

### Files
- <N> source files modified/created
```

---

