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
| [comm_bus_only_inter_plc_channel.md](comm_bus_only_inter_plc_channel.md) | `relay/runtime/`, scenarios | All inter-PLC coordination flows through `CommBus`; no side channels |
| [verification_path_purity.md](verification_path_purity.md) | `relay/verify/` | `verify/` has a closed import set — no LLM, no I/O, no transitive dependencies on either |
| [scan_phase_isolation.md](scan_phase_isolation.md) | `relay/runtime/`, `relay/st/`, plants, comm strategies | Per-scan phase order is fixed; ST execution is a pure function of its inputs |
| [simclock_only_time_source.md](simclock_only_time_source.md) | execution-path modules | All time in execution-path code derives from injected `SimClock` or `dt_ms` |

## Adding an invariant

1. Draft the file using the section structure above.
2. Add a row to the index.
3. Reference it from CLAUDE.md `## Don't` or relevant skill files where it
   would change agent behavior.
4. Commit alongside the code change that motivated it (or as its own commit
   if codifying an existing implicit rule).
