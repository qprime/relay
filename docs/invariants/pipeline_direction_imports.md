# Invariant: Imports follow pipeline data flow

**Status:** Active | **As-Of:** 2026-05-07 | **Scope:** all `relay/` pipeline stages

## Statement

The relay pipeline has a fixed data-flow order:

```
spec → generator → st → {runtime, plant} → verify
```

Each stage may import only from stages upstream of it in this order, plus
stage-neutral leaf modules under `relay/strategies/`. Backward edges across
stages are forbidden.

When a downstream stage owns logic an upstream stage needs (e.g., per-strategy
spec validation), that logic must be extracted to a leaf module under
`relay/strategies/` that both stages import from. The fix is structural, not
documentary.

## Why

The pipeline diagram is a load-bearing claim about the architecture, not a
narrative convenience. Each stage is supposed to be a self-contained pass
that consumes upstream artifacts and produces a downstream one. That property
is what lets:

- a future contributor read one stage in isolation and form a correct mental
  model of its inputs and outputs;
- a refactor of a downstream stage proceed without rippling into upstream
  stages;
- a new stage be added (or an old one replaced) without re-validating every
  cross-stage edge in the codebase.

The moment `spec/` imports from `runtime/`, the pipeline diagram is no
longer the import graph. Anyone reading the diagram has to also remember
the documented exceptions, and the rule "stages depend only on what's
upstream" silently becomes "stages depend on what's upstream, except where
we wrote a paragraph saying otherwise."

Documented exceptions don't compose. The second exception is harder to
reason about than the first; the fifth exception means there is no rule.
Mechanically forbidding backward edges keeps the diagram and the import
graph identical for the lifetime of the project.

## What this looks like

1. **Stage order is fixed**: `spec → generator → st → {runtime, plant} → verify`.
   `runtime/` and `plant/` are siblings; either may import from the other
   (the runtime drives the plant per-scan).
2. **Leaf modules are stage-neutral.** Top-level modules under `relay/`
   that are not pipeline stages — `relay/clock.py`, `relay/io_image.py`,
   `relay/trace.py`, `relay/trace_io.py`, `relay/verdict_io.py`, and anything
   under `relay/strategies/` — hold shared
   types, Protocols, and registries that any stage may import. Leaf modules
   may import only from stdlib and from other leaves; they may not import
   from any pipeline stage. They exist precisely to break what would
   otherwise be a backward edge.
3. **Per-strategy validation lives on the strategy.** When `spec/` needs to
   validate a strategy-specific spec block, it resolves the strategy via
   `relay.strategies.<subsystem>.get_<subsystem>(name)` and dispatches to
   the strategy's validator. It does not import the strategy's runtime
   implementation directly.
4. **Tests are unconstrained.** `tests/` may import from any stage —
   constructing a scenario commonly requires the full pipeline.
5. **Mechanical check.** A test or pre-commit hook walks each stage's
   imports and fails on any cross-stage backward edge.

## What violates this invariant

- `from relay.runtime.* import ...` inside `relay/spec/`.
- `from relay.generator.* import ...` inside `relay/spec/`.
- `from relay.runtime.* import ...` inside `relay/st/` (st is upstream of
  runtime; runtime drives st, not the other way around).
- `from relay.plant.* import ...` inside `relay/spec/`, `relay/generator/`,
  or `relay/st/`.
- `from relay.verify.* import ...` inside any non-test module — verify is
  the final stage; nothing else may depend on it.
- A leaf module (`relay/clock.py`, `relay/io_image.py`, `relay/trace.py`,
  `relay/trace_io.py`, `relay/verdict_io.py`, `relay/strategies/*`) that
  imports from a pipeline
  stage. Leaves are
  leaves: stdlib + sibling leaves only.

## What is NOT covered by this invariant

- **Direction within a stage.** Files inside `relay/runtime/` may import
  from each other freely. The rule is about cross-stage edges.
- **`runtime/` ↔ `plant/`.** They are siblings in the data flow (one scan
  drives one plant step). Either may import from the other.
- **Test code.** `tests/` may import anything. Verification of the
  invariant runs *as* a test against the `relay/` package.
- **Stdlib and third-party imports.** The rule is about intra-`relay/`
  edges, not external dependencies (those are constrained by other
  invariants, e.g. `verification_path_purity`).

## Failure mode this prevents

A contributor adds runtime-side validation to a strategy block in the
spec, naturally reaches into `relay/runtime/` for the validator, and adds
`from relay.runtime.<thing> import <validator>` to `relay/spec/schema.py`.
Tests pass. The diff is small. They merge it.

A few months later, another contributor refactors `relay/runtime/` —
splits a module, renames a class, changes a return type. The build breaks
in `relay/spec/`. They debug, get confused (why does spec parsing depend
on the runtime?), make the spec change, and now there's a second backward
edge to match the first.

A year later, a new contributor reads the README pipeline diagram, sees
`spec → generator → ... → verify`, and tries to add a stage between spec
and generator. Their stage needs to call into spec — fine, that's
upstream. But now they're hit with the backward edges spec accumulated;
their stage transitively depends on runtime, plant, and a config loader
that wasn't in the diagram at all. The "small isolated stage" they were
adding becomes a refactor of the whole import graph.

The diagram has stopped describing the code. Recovering the property is
expensive — every stage has to be audited, every backward edge replaced
with a leaf module or removed entirely. Cheaper to never let the first
edge exist.

## Examples in this codebase

- **Shared data types as leaves**: `SimClock` ([relay/clock.py](../../relay/clock.py)),
  `IOImage` ([relay/io_image.py](../../relay/io_image.py)), and the trace
  log ([relay/trace.py](../../relay/trace.py)) live at the top level of
  `relay/`. `runtime/`, `plant/`, and `verify/` all import them; none of
  them imports from a pipeline stage.
- **Comm strategy registry** ([relay/strategies/comm.py](../../relay/strategies/comm.py))
  — Protocol + registry, leaf module. `relay/spec/schema.py` imports it
  for spec-time validation; `relay/runtime/` imports it (or will) for
  strategy-aware bus operations. Neither stage imports from the other.
- **Verifier import set** ([verification_path_purity.md](verification_path_purity.md))
  — a tighter version of this invariant scoped to `relay/verify/`. That
  invariant predates this one and remains in force; this invariant
  generalizes the underlying principle to all stages.

## Enforcement (suggested mechanical check)

A test that walks `relay/<stage>/**.py` for each stage, parses imports,
and fails on any `relay.<other-stage>` import where `<other-stage>` is
not upstream in the pipeline order (or a sibling, for runtime/plant).
Reuse the parsing approach from any verification_path_purity check.

## Related

- [verification_path_purity.md](verification_path_purity.md) — narrower
  scope (`verify/` only), tighter rule (full allowlist, not just direction)
- [pluggable_subsystems.md](pluggable_subsystems.md) — strategies own
  validation; this invariant constrains *where* strategies can live so
  that ownership doesn't create backward edges
- CLAUDE.md `## Don't` — "Backward-edge imports across pipeline stages"
