# Invariants

Project-level invariants for relay. Each invariant is a documented rule the
framework holds itself to — usually because violating it would silently degrade
simulation fidelity, determinism, or the verification guarantee.

## Convention

- One invariant per file. Filename describes the rule (`snake_case.md`).
- Each invariant file states: **Status**, **Scope**, **Statement**, **Why**,
  **What this looks like**, **What violates this invariant**, **What is NOT
  covered**, **Failure mode this prevents**.
- Invariants are referenced from CLAUDE.md and from skill files when they
  constrain a decision the agent is making.
- Invariants are amended, not deleted. If an invariant no longer holds, mark
  **Status: Retired** with the reason and the commit that retired it.

## Index

| File | Scope | Summary |
|------|-------|---------|
| [pluggable_subsystems.md](pluggable_subsystems.md) | framework-wide | Pluggable subsystems use `Protocol` + registry, selected by explicit task-spec field |

## Adding an invariant

1. Draft the file using the section structure above.
2. Add a row to the index.
3. Reference it from CLAUDE.md `## Don't` or relevant skill files where it
   would change agent behavior.
4. Commit alongside the code change that motivated it (or as its own commit
   if codifying an existing implicit rule).
