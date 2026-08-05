# Invariant: Pluggable subsystems use strategy + registry

**Status:** Active | **As-Of:** 2026-04-21 | **Scope:** framework-wide

## Statement

Any subsystem whose behavior depends on a domain-level choice — a real-world
protocol, model, or convention with multiple legitimate implementations — must
be implemented as a `Protocol`-typed strategy resolved through a registry,
selected by an explicit field in the task spec.

The framework code calls the strategy. It does not branch on the chosen name,
inspect the chosen variant, or hold defaults that depend on which variant is
active.

## Why

RELAY's value is fidelity to control-systems reality. That reality has multiple
right answers for many subsystems:

- **Comm**: tag-based (Logix, modern engineering tools), address-based
  (Modbus TCP register maps), pub/sub (OPC UA), process-data (EtherCAT).
- **Plant**: conveyor, tank, robot cell, batch reactor — each has its own
  physics shape and sensor/actuator surface.
- **Clock semantics** (future): synchronous scan, free-running, PTP-disciplined.
- **ST execution mode** (future): scan, event-driven, sequential function chart.

Hard-coding one variant — or worse, branching on `if comm == "modbus": ...` —
makes the framework unable to model the next scenario without core surgery,
collapses validation rules into one common-denominator set, and biases the
generator toward whatever shape the `if` branch happened to handle first.

Strategy + registry keeps the framework honest about *which* model of reality a
given simulation is running, and keeps each strategy's invariants enforceable
on its own terms.

## What this looks like

For every pluggable subsystem:

1. **A `Protocol` in the subsystem package** declaring the minimum surface the
   harness needs (e.g. `CommStrategy`, `PlantProtocol`).
2. **One concrete implementation per real-world variant**, each in its own
   module.
3. **A registry** (`get_<subsystem>(name) -> Protocol`) populated by
   import-time registration. No central import list — variants self-register.
4. **An explicit selector field in the task spec** (e.g. `Comm.strategy`,
   `Plant.type`) that the harness reads to resolve the strategy.
5. **Per-variant validation**: each strategy owns the validation rules for
   its own spec block. The spec validator dispatches to
   `strategy.validate_config(block)`; it does not know what makes a tag map
   valid versus a register map.
6. **Per-variant authoring context, sourced from the strategy**: whoever
   authors a spec block — a person or an agent in conversation — works
   against the rules the strategy itself owns in `validate_config`, not
   against a prompt fragment stored on the strategy. Authoring is external
   to this repo; the strategy's validator is what makes it converge, via
   `python -m tools.validate_spec`. A spec declares one strategy and carries
   that strategy's idiom, never the union of all strategies' fields.

## What violates this invariant

- A `match`/`if` chain on a strategy name anywhere outside the registry lookup
  itself.
- A "default" strategy that the harness silently falls back to when the spec
  doesn't declare one. Spec must declare; harness must raise on missing.
- Validation rules for a specific variant living in shared validator code
  rather than on the strategy.
- Authoring context that lists all strategies' fields and leaves the choice
  to whoever is writing the spec. The spec declares the strategy; the block
  carries that strategy's idiom alone.
- Cross-strategy coupling — a comm strategy that knows it's running with a
  particular plant, or vice versa.

## What is NOT covered by this invariant

- Internal implementation choices with one right answer (data structure
  selection, algorithm choice within a single function). Those are local,
  not multi-variant.
- Single-variant subsystems where no second variant is plausible. Don't build
  a registry for the verification assertion DSL just because you could —
  there is one assertion grammar, owned by `verify/assertions.py`. If a
  second grammar arrives, *then* extract the strategy.

## Failure mode this prevents

The framework grows a second scenario, the second scenario needs a different
comm model, and a contributor adds `if spec.System.comm == "opcua": ...` in
the harness. Six months later that branch has metastasized: the plant module
checks it (because OPC UA changes how actuators acknowledge), the validator
checks it (because tag rules don't apply), and the ST generator grows a
six-bullet conditional. The framework now models one and a half protocols
badly instead of two protocols correctly.

## Examples in this codebase

- **Comm strategies**: registry in [relay/strategies/comm.py](../../relay/strategies/comm.py)
  (`build_comm_strategy` / `get_comm_strategy`), selected by `Comm.strategy` in
  the task spec; raises on unknown. Today two strategies are registered:
  `tag` (live; used by the conveyor demo) and `address` (a stub whose
  `validate_config` and `route` raise `NotImplementedError`, reserved for a
  future Modbus TCP-style register-map implementation). The registry lives in
  `relay/strategies/` (a leaf module) rather than `relay/runtime/` so
  `relay/spec/` can import it for validation without violating
  [pipeline_direction_imports.md](pipeline_direction_imports.md).
  **(Planned)** Split into `relay/strategies/comm/{tag,address}.py` with
  per-module self-registration if a third strategy lands.
- **Plant models**: registry in
  [relay/strategies/plant.py](../../relay/strategies/plant.py)
  (`register_plant` / `get_plant`), selected by `Plant.type` in the task
  spec. Concrete physics lives in `relay/plant/<name>.py` and self-registers
  at import time — see `register_plant("conveyor", ...)` at the bottom of
  [relay/plant/conveyor.py](../../relay/plant/conveyor.py). The harness in
  `relay/runtime/harness.py` imports `relay.plant` to trigger registration,
  then calls `get_plant(spec.plant_type)`. Additional plant types are
  aspirational; only `conveyor` is live today.

## Related

- CLAUDE.md `## Don't` — local rules, conventions
- Generator spec — first concrete consumer of this invariant (comm strategy
  selection drives validator dispatch)
