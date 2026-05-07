# QPrime Python Coding Convention

**Status:** Standard | **Version:** 2.0 | **Scope:** all Python in QPrime / TenneCNC projects.

This document exists so an agent generating Python writes code that type-checks, passes review, and survives the FFI on the first try. Read the Quick Lookup table first; load the rest by reference when a trigger sends you there. Loaded terms (*wrapper type*, *result type*, *trap*, *registry dispatch*) are deliberate — grep for them.

---

## Quick Lookup

When you're about to write... go to...

| Situation | Section |
|-----------|---------|
| New data structure | [Pattern: frozen dataclass](#pattern-frozen-dataclass) |
| Mutating a field on an existing instance | [Trap: dataclass mutation](#trap-dataclass-mutation) |
| `value or default` for a parsed numeric | [Trap: the `or` fallback](#trap-the-or-fallback) |
| Function that may fail | [Pattern: failure mechanism](#pattern-failure-mechanism) |
| Optional operation that returns nothing legitimately | [Pattern: expected-failure protocol](#pattern-expected-failure-protocol) |
| Function with structural precondition (convex, sorted, non-empty) | [Pattern: wrapper type for preconditions](#pattern-wrapper-type-for-preconditions) |
| `try/except` to choose a code path | [Trap: exceptions as control flow](#trap-exceptions-as-control-flow) |
| Dispatch on a type tag, feature name, or string identifier | [Pattern: registry dispatch](#pattern-registry-dispatch) |
| `if/elif/else` chain over `isinstance(x, T)` | [Pattern: exhaustive union dispatch](#pattern-exhaustive-union-dispatch) |
| `to_dict()` method that drops a field | [Trap: serializer drop](#trap-serializer-drop) |
| `print()` in code imported by other modules | [Trap: print in library code](#trap-print-in-library-code) |
| Function with more than 3 related parameters | [Pattern: params object](#pattern-params-object) |
| List in a frozen dataclass field | [Pattern: tuple collections](#pattern-tuple-collections) |
| Comprehension with a filter or fallible call | [Pattern: explicit loop](#pattern-explicit-loop) |
| Adding a new field to a serialized type | [Pattern: round-trip completeness](#pattern-round-trip-completeness) |
| Inline numeric literal in a check | [Pattern: named constants](#pattern-named-constants) |
| New unit (mm, sec, deg) | [Pattern: single unit system](#pattern-single-unit-system) |
| Picking `Enum` vs string vs `Literal` | [Pattern: type system mechanisms](#pattern-type-system-mechanisms) |
| New custom exception class | [Pattern: exception types](#pattern-exception-types) |
| Editing pybind11 binding or schema shared with C++ | [FFI Conventions](#ffi-conventions) |
| Adding an import to a low-level module | [Pattern: dependency direction](#pattern-dependency-direction) |
| Test for a function that produces structured output | [Testing](#testing) (golden-tested IR) |
| Setting up a module-level logger | [Logging](#logging) |
| Choosing a verb for a new function | [Naming Vocabulary](#naming-vocabulary) |
| Adding a layer-specific failure mode | [Error Semantics by Layer](#error-semantics-by-layer) |
| `def f(x=[])` or `def f(x={})` | [Trap: mutable default argument](#trap-mutable-default-argument) |
| `except Exception:` or bare `except:` | [Trap: broad except](#trap-broad-except) |
| `Any` annotation, `# type: ignore` without a code | [Trap: silencing the type checker](#trap-silencing-the-type-checker) |
| `dict[str, Any]` as a return or parameter type | [Trap: untyped dict as escape hatch](#trap-untyped-dict-as-escape-hatch) |

---

## Patterns

### Pattern: Frozen Dataclass

All core data structures are frozen dataclasses. Immutability prevents shared-state corruption and lets the type checker catch what mutation would mask.

```python
@dataclass(frozen=True)
class Measurement:
    width_mm: float
    height_mm: float
```

**Field ordering** (strict — avoids the "non-default argument follows default argument" error):

1. **Required fields** (no default) — essential identity
2. **Optional typed fields** (`field: Type | None = None`)
3. **Factory-default fields** (`field(default_factory=...)`)
4. **Scalar defaults** (`field: Type = value`)

```python
@dataclass(frozen=True)
class Feature:
    name: str                                                  # required
    width_mm: float                                            # required
    description: str | None = None                             # optional
    tags: tuple[str, ...] = field(default_factory=tuple)       # factory
    enabled: bool = True                                       # scalar
```

**Construction-time validation.** Constraints are enforced in `__post_init__`, not checked later by callers.

```python
@dataclass(frozen=True)
class Box:
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.width <= 0:
            raise ValueError(f"Box: width must be > 0, got {self.width}")
        if self.height <= 0:
            raise ValueError(f"Box: height must be > 0, got {self.height}")
```

**`with_*()` helpers** for common mutation patterns wrap `replace()` so the call site stays readable:

```python
def with_tags(self, *new_tags: str) -> Feature:
    return replace(self, tags=self.tags + new_tags)
```

### Pattern: Tuple Collections

Collections in frozen dataclasses are `tuple`, not `list`. Tuples signal immutability and prevent accidental `append` / `remove` / `sort`.

```python
@dataclass(frozen=True)
class Layout:
    items: tuple[Item, ...]
    boundaries: tuple[Point, ...]
```

Internal accumulation uses `list` (for `.append()`); convert to `tuple` at the return boundary:

```python
def collect_items(source) -> tuple[Item, ...]:
    result: list[Item] = []
    for raw in source:
        result.append(Item.from_raw(raw))
    return tuple(result)
```

Empty default uses literal `()`. Use `field(default_factory=tuple)` only for non-empty or complex defaults.

### Pattern: Wrapper Type for Preconditions

Scalar preconditions (positive width, non-empty string) belong on the type that owns them. *Structural* preconditions (convex polygon, sorted range, non-empty buffer, monotonic sequence, oriented loop with known winding) get a *wrapper type*. The function's signature then proves the precondition.

**Fails when missing:** every algorithm assuming the precondition re-checks it (or worse, doesn't, and produces wrong output on bad input).

```python
@dataclass(frozen=True)
class ConvexPolygon:
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if not _is_convex(self.points):
            raise ValueError(f"ConvexPolygon: points are not convex, got {len(self.points)} vertices")

    @classmethod
    def try_from(cls, points: tuple[Point, ...]) -> ConvexPolygon | None:
        try:
            return cls(points)
        except ValueError:
            return None


def inset(poly: ConvexPolygon, offset: float) -> Polygon:
    ...
```

`inset`'s signature *proves* the precondition. A non-convex polygon cannot reach `inset` without going through `try_from`. The check happens once.

Apply when the precondition is structural and the wrapped value is otherwise a generic collection or primitive that other code might pass unwrapped.

### Pattern: Failure Mechanism

| Mode | Use When |
|------|----------|
| Raise exception | Hard error: invalid input, constraint violation. Caller didn't anticipate the failure. |
| Return collection (possibly empty) | Operation that legitimately produces zero, one, or many results. |
| Raise `SkipError` (see [Pattern: expected-failure protocol](#pattern-expected-failure-protocol)) | Soft failure: optional operation, absence is acceptable, caller may want to distinguish skip from bug. |
| `Optional[T]` return | Search that may not find; constructor that may fail (`try_from`). |
| Silent partial output | Never. |

**Operations that can produce zero, one, or many results return a collection** — never `T | list[T]`:

```python
def split(region: Region) -> list[Region]:
    ...
```

**Error messages include four parts:**

1. **What failed** — class or subsystem
2. **What field** — specific parameter
3. **What constraint** — the rule that was broken
4. **Actual value** — what was received

```python
raise ValueError("SheetConfig: width_mm must be > 0, got -3.5")
```

### Pattern: Expected-Failure Protocol

Some failures are normal: a region too small to machine, a constraint that can't be satisfied, an optional operation with no work to do. These need a dedicated exception type so callers distinguish "expected skip" from "actual bug."

```python
class SkipError(ValueError):
    """Operation impossible for this input — expected condition, not a bug."""


def generate(domain, params, *, allow_empty=False):
    if domain.area < min_area(params):
        if allow_empty:
            return []
        raise SkipError(f"Domain area {domain.area} below minimum {min_area(params)}")
    return do_work(domain, params)
```

Callers choose tolerance via `allow_empty`:
- **Strict** (`allow_empty=False`): raises — caller must handle
- **Lenient** (`allow_empty=True`): returns empty — caller gets no output but no exception

The layer above always catches `SkipError` and continues. It never propagates past the immediate caller.

### Pattern: Exhaustive Union Dispatch

Match/if-else chains over union types must handle every variant. Adding a new variant should cause visible failures, not silent fallthrough.

```python
FaceFeature = DrillHole | SquareMortise | CarvedDesign | GeometricPattern


def process(feature: FaceFeature) -> Output:
    if isinstance(feature, DrillHole):
        return process_hole(feature)
    elif isinstance(feature, SquareMortise):
        return process_mortise(feature)
    elif isinstance(feature, CarvedDesign):
        return process_carving(feature)
    elif isinstance(feature, GeometricPattern):
        return process_pattern(feature)
    else:
        raise TypeError(f"Unhandled feature type: {type(feature).__name__}")
```

The final `else: raise TypeError(...)` turns silent bugs into immediate, diagnosable failures when a new variant is added to the union. Same pattern for `match` statements with a wildcard `case _:`.

### Pattern: Registry Dispatch

Dispatch on a type tag or feature name uses a registry dict, not an if/elif chain. Registries are declarative, extensible, and self-documenting.

**Fails when missing:** the if/elif chain grows past five branches, normalization logic gets duplicated into each handler, and a new tag added in one place is silently absent from the dispatch.

```python
HANDLERS: dict[str, Callable] = {}


def register(feature_type: str):
    def decorator(fn):
        HANDLERS[feature_type] = fn
        return fn
    return decorator


@register("pocket")
def handle_pocket(data): ...


def handle(feature_type: str, data):
    handler = HANDLERS.get(feature_type.lower())
    if handler is None:
        raise ValueError(f"Unknown feature type: {feature_type}")
    return handler(data)
```

**Normalize at the entry point** (`.lower()`, `.strip()`, `float()`, `None`-resolution), not inside each handler. Once at function entry; all branches see canonical input.

### Pattern: Params Object

A function with more than 3 related parameters takes a frozen dataclass instead. This prevents argument-order bugs the type checker can't catch and gives validation a natural home.

```python
@dataclass(frozen=True)
class GridParams:
    width_mm: float
    height_mm: float
    depth_mm: float
    spacing_mm: float
    offset_mm: float = 0.0
    angle_deg: float = 0.0


def generate(domain: Domain, params: GridParams, *, allow_empty: bool = False):
    ...
```

Use `*` to force keyword-only arguments for flags and options that would be ambiguous as positional (`allow_empty`, `strict`, etc.).

### Pattern: Guard Clauses

Functions validate preconditions with early `raise` before doing real work. Happy path stays unindented.

```python
def process(items, config):
    if not items:
        return []
    if not config.is_valid():
        raise ValueError(f"Config: must be valid, got {config}")
    for item in items:
        ...
```

### Pattern: Explicit Loop

Comprehensions are for simple infallible transforms. Anything with per-item error handling, conditional logic, or multi-step processing uses an explicit loop:

```python
items: list[Item] = []
warnings: list[str] = []
for x in inputs:
    try:
        items.append(process(x))
    except ValueError as e:
        warnings.append(f"Skipped {x.id}: {e}")
```

Comprehensions are fine for simple shapes: `names = [item.name for item in items]`.

**Preserve input order.** Output order matches input order by default. For deduplication, use a dict keyed by identity to keep first-seen order:

```python
seen: dict[Key, Item] = {}
for item in items:
    key = item.identity_key()
    if key not in seen:
        seen[key] = item
unique_items = tuple(seen.values())
```

### Pattern: Round-Trip Completeness

`to_dict()` serializes all non-private fields. Adding a new field updates the serializer, the deserializer, and the round-trip test.

```python
def to_dict(self) -> dict:
    return {
        "name": self.name,
        "width": self.width,
        "height": self.height,
    }
```

If a field is intentionally omitted, the serialization site carries a comment explaining why. Silent omission is a data-loss bug.

**Round-trip assertion is semantic, not syntactic:** `parse(serialize(model)) == model`, not `serialize(parse(text)) == text`. Whitespace, key order, and equivalent representations may legitimately differ.

**Non-default emission.** Formatters emit only non-default fields; parsers accept both detailed and simplified forms. The formatter emits the simplest valid representation; the parser accepts the most permissive.

### Pattern: Type System Mechanisms

| Mechanism | Use When | Example |
|-----------|---------|---------|
| `Enum` with `auto()` | Internal identity types (value doesn't matter) | `Role.ADMIN`, `Status.ACTIVE` |
| `Enum` with string values | Serialized or user-facing values | `Mode("fast")`, `Verdict("pass")` |
| `Literal[...]` | Inline field constraints on dataclass fields | `side: Literal["left", "right"]` |
| Constants class | String keys for dict lookup and dispatch (extensible without code change) | `class FeatureType: POCKET = "pocket"` |
| Pipe union (`A \| B`) | Sum types at module level | `Event = Click \| Hover \| Scroll` |
| `@runtime_checkable Protocol` | Structural subtyping interfaces | `class Handler(Protocol): def handle(self) -> None: ...` |

Decision rule for closed vs. open sets:

| Question | Use |
|----------|-----|
| Adding a new value requires a new code path | `Enum` |
| Set is defined by the program's logic | `Enum` |
| New value can be added without changing Python code | `str` |
| Set is defined by external data (config, schema) | `str` |

`Protocol` over `ABC` for interfaces — structural subtyping doesn't require inheritance. ABC only when shared implementation is genuinely needed.

### Pattern: Exception Types

Use Python's built-in exceptions consistently:

| Exception | Use When |
|-----------|----------|
| `ValueError` | Right type, violates constraint (negative width, empty string, out of range) |
| `TypeError` | Wrong type entirely (passed string where int was expected, unhandled union variant) |
| `KeyError` | Required key missing from a mapping |
| `RuntimeError` | "Shouldn't happen" state reached at runtime (invariant violation, impossible branch) |
| `NotImplementedError` | Method exists in interface but subclass hasn't implemented |
| `FileNotFoundError` | Expected file doesn't exist |

**Custom exceptions** when callers need to distinguish your errors for recovery. One custom base per subsystem is usually enough.

```python
class ValidationError(ValueError):
    """A constraint check failed during validation."""


class PipelineError(RuntimeError):
    """An invariant was violated during pipeline execution."""
```

The error message carries specifics. The exception type carries the category. No exception class explosion (`WidthTooSmallError`, `HeightTooSmallError`, etc.) — that's a class hierarchy in disguise.

### Pattern: Named Constants

Named module-level constants for magic numbers. Inline literals make code unmaintainable and produce inconsistencies when the same value reappears.

```python
MIN_MARGIN_MM = 10.0

if margin < MIN_MARGIN_MM:
    ...
```

Trivially obvious literals (`0`, `1`, array indices, loop bounds tied to a local container) don't need names.

### Pattern: Single Unit System

Pick one unit and use it everywhere. No runtime conversions, no mixed units, no unit suffixes on variable names except at system boundaries to disambiguate.

**Fails when missing:** the same value gets converted twice (once on entry, once mid-pipeline) or not at all; bugs surface as off-by-25.4 errors that look like geometry problems.

```python
width_mm = 101.6
```

External input arrives in whatever unit the source uses; convert at the boundary, never internally.

**Coordinate spaces.** When multiple coordinate spaces exist (local, world, screen), name them and document the transforms. Internal code operates in one canonical space; transforms happen at well-defined boundaries (import, export, rendering). Never apply the same transform twice.

### Pattern: Pure Transformations

Functions that transform data are pure: same input produces same output, no side effects, no input mutation.

**Fails when missing:** tests become flaky, debugging becomes guessing, reproductions stop being reproducible. Mutation through a shared reference is the most expensive bug class to find.

```python
def process(domain: Domain) -> list[Item]:
    # domain is never modified
    return items
```

### Pattern: Pipeline Architecture

Multi-stage data transforms keep stages separate. Each layer has a single responsibility and a well-defined contract with adjacent layers.

**Fails when missing:** validation gets bypassed because a path skipped a stage; backend-specific details leak into the semantic layer and lock the project to one backend; tests have to run the full pipeline because the stages aren't independently testable.

```python
model = parse(raw_input)
validated = validate(model)
output = render(validated)
```

**Intermediate representations** are validation checkpoints between parsing and output generation. The IR describes *what*, not *how*. Multiple backends consume the same IR; tests target the IR (fast, focused) rather than the full pipeline (slow, brittle).

**No pass-through of computed data.** Semantic data structures describe *what*, not *how*. Implementation details (computed geometry, backend-specific offsets, rendering hints) live in the adapter between layers, not threaded through the semantic layer.

### Pattern: Dependency Direction

Imports flow downward:

```
Input/CLI  →  Parser  →  IR/Model  →  Validation  →  Backend/Output
```

Each layer may import from layers to its right, never to its left.

**Fails when missing:** circular imports, the data model carrying a dependency on the renderer, and the impossibility of running the lower layers in isolation for tests or alternate backends.

| Trigger | Action |
|---------|--------|
| Lower layer needs a higher layer's type | Adapter at the boundary; do not pull higher-layer imports down. |
| Wrapper type used by multiple layers | Lives at the layer that owns the precondition. |
| Ambiguity check | Delete the higher-level module mentally — does the lower-level module still import? If not, dependency is inverted. |

```python
# model.py — no renderer dependency
@dataclass(frozen=True)
class Feature:
    style: str

# adapter.py — bridges the gap
from model import Feature
from renderer.types import RenderHint

def feature_to_render_hint(feature: Feature) -> RenderHint:
    return STYLE_MAP[feature.style]
```

---

## Traps

Each trap names what AI generation reaches for by default and the rule that contradicts it.

### Trap: dataclass mutation

`item.field = value` on a frozen dataclass raises `FrozenInstanceError`. On a non-frozen dataclass it silently corrupts shared state — the bug nobody finds until production.

**Use** `replace()`:

```python
new_item = replace(item, width_mm=100)
```

Frozen dataclasses are shallow: nested dicts and lists remain technically mutable. Treat them as immutable too — create new collections, never mutate in place.

```python
new_item = replace(item, metadata={**item.metadata, "processed": True})
```

### Trap: the `or` fallback

`value or default` is broken for nullable numerics where `0` is a valid value. Python's `or` treats `0`, `0.0`, and `""` as falsy, silently substituting the default.

**Use** an explicit `is None` check:

```python
width = data.get("width")
if width is None:
    width = default_width
```

Applies to all parsed input (YAML, JSON, CLI args, database reads). Same trap applies to string fields where `""` is valid.

### Trap: exceptions as control flow

`try/except` to choose a code path that a conditional would express more clearly is a misuse. Exceptions signal something the caller didn't anticipate; routine branching uses `if`.

The one carve-out is the [expected-failure protocol](#pattern-expected-failure-protocol) — `SkipError` is a deliberate signal that the layer above is expected to catch. That's the rule, not the start of a list.

### Trap: serializer drop

`to_dict()` that omits a field silently corrupts data on round-trip. The deserialized object is missing the field; downstream code uses the default; the original value is gone.

**Use** [round-trip completeness](#pattern-round-trip-completeness) — serialize all non-private fields; document any deliberate omission with a comment at the serialization site.

### Trap: print in library code

`print()` belongs in CLI entry points (`cli/`, `main()`, `if __name__ == "__main__":` blocks) and one-off scripts. A function imported by other modules has no business writing to stdout — that's the caller's job.

The failure this prevents: a `print()` snuck into a deep helper for debugging, never removed, now spamming stdout every pipeline run.

**Use** the `logging` module:

```python
import logging
_logger = logging.getLogger(__name__)


def process(items):
    _logger.info("Processing %d items", len(items))
    for item in items:
        _logger.debug("Processing item: %s", item.name)
    return results
```

Parametrized formatting (`"%s", value`) — not f-strings — so the level filter can skip formatting for suppressed messages.

### Trap: mutable default argument

`def f(x=[])` or `def f(x={})` evaluates the default once at function-definition time. Every call that omits the argument shares the same object. Mutations leak across calls.

**Use** `None` as the sentinel and resolve inside the function:

```python
def collect(items: list[Item], extra: list[Item] | None = None) -> list[Item]:
    if extra is None:
        extra = []
    return items + extra
```

Same rule for `dict`, `set`, and any other mutable type. Frozen-dataclass and tuple defaults are safe.

### Trap: broad except

`except Exception:` and bare `except:` catch what you didn't mean to catch — `KeyboardInterrupt`, `MemoryError`, an exception from a future code path that should have surfaced. The handler swallows real bugs and turns them into silent skips.

**Use** the narrowest exception type that names what you're handling:

```python
try:
    value = parse_dimension(raw)
except ValueError as e:
    warnings.append(f"Skipped {raw}: {e}")
```

If you genuinely need to catch everything (top-level CLI handler, supervisor loop), `except Exception:` is acceptable *only* with re-raise or explicit logging plus a comment naming why the breadth is required. Bare `except:` is never acceptable.

### Trap: silencing the type checker

`Any` in a signature, or `# type: ignore` without a specific error code, hides the type problem rather than solving it. The next reader (and the next agent) loses the constraint the annotation was supposed to carry.

**Use** the actual type. If the type is genuinely union-shaped, write the union. If a third-party library is untyped, `# type: ignore[import-untyped]` with the specific code at the import site keeps the rest of the module strict.

```python
# Right
def parse(raw: str) -> Dimension | None: ...

import untyped_lib  # type: ignore[import-untyped]

# Wrong
def parse(raw: str) -> Any: ...

result = compute()  # type: ignore
```

`Any` at FFI boundaries is acceptable when pybind11 hasn't declared a stub; isolate it to the boundary, don't propagate it inward.

### Trap: untyped dict as escape hatch

`dict[str, Any]` as a return type or parameter type is a frozen dataclass that hasn't been written. The caller has to remember which keys exist; the type checker can't help; serialization round-trips silently drop fields.

**Use** a frozen dataclass:

```python
# Right
@dataclass(frozen=True)
class ToolConfig:
    name: str
    diameter_mm: float
    flute_count: int

def load_tool(path: Path) -> ToolConfig: ...

# Wrong
def load_tool(path: Path) -> dict[str, Any]: ...
```

`dict[str, Any]` is appropriate at parser entry (raw YAML, raw JSON) and only there. The next layer converts to a dataclass.

---

## Error Semantics by Layer

Different layers handle errors differently. The principle: failures become less fatal as you move outward.

| Layer | On Failure | Mechanism |
|-------|-----------|-----------|
| Parser | Fail hard | Raise a parse error; malformed input is not recoverable |
| Resolver / Builder | Skip item | Catch expected-failure exceptions; continue with remaining items |
| Adapters | Warn + skip | Catch `ValueError` per item; log warning, append to warnings list, continue |
| Core engine / Planner | Warn + skip | Log to structured accumulator; skip item, continue job |
| Pipeline orchestrator | Collect + gate | Accumulate errors/warnings from all layers; halt on safety-critical failures |

**Per-item isolation.** Loops processing collections wrap each item in `try/except`. One bad item never kills the batch.

```python
results: list[Item] = []
warnings: list[str] = []
for item in items:
    try:
        results.append(process(item))
    except ValueError as e:
        warnings.append(f"Skipped {item.id}: {e}")
```

**Structured warning collection.** When an item is skipped, emit both a `_logger.warning(...)` for developer diagnostics and a `warnings.append(msg)` for the pipeline result. Pure logging is only for diagnostics that don't affect correctness; if the skip affects final output, it must reach the structured warnings.

Warning messages always include: item identity, specific problem, action taken (`"— skipped"`).

---

## Testing

| Rule | Mechanism |
|------|-----------|
| Test at the level the logic lives | Unit tests target the function or class containing the behavior, not the full pipeline. Pipeline tests are integration only. |
| Test project code, not the language | No tests for `@dataclass(frozen=True)` raising on mutation, or `replace()` working — that's testing Python. Test what *your code* adds. Test `__post_init__` only if it has custom validation. |
| Round-trip tests assert semantic equivalence | `parse(serialize(model)) == model`. Whitespace, key order, equivalent representations may legitimately differ. |
| No duplicate coverage | Two tests asserting the same behavior over the same input is a defect. Check existing tests before adding a new file. |
| Use the framework | `pytest` discovers tests in `tests/`. No `if __name__ == "__main__":` blocks, no `print("PASS")`, no `return True` from test functions, no `sys.path` manipulation. |
| Limit expensive iteration | If a higher-level test already runs the full fixture set, don't add redundant loops. One iteration per validation concern. |
| Golden-tested IR | Computations producing structured output (toolpaths, plans, schedules, traces, generated code) have golden-tested IRs. Every change is either (a) no golden change — refactor proven, or (b) deliberate snapshot regeneration with the diff explained in the commit message. |

---

## Logging

| Rule | Mechanism |
|------|-----------|
| No `print()` in library code | See [Trap: print in library code](#trap-print-in-library-code). |
| Use `logging.getLogger(__name__)` | Module-level: `_logger = logging.getLogger(__name__)`. Underscore prefix marks it private. |
| Parametrized formatting | `_logger.warning("message %s", value)`, not f-strings. Level filter skips formatting for suppressed messages. |

Level discipline:

| Level | Use When |
|-------|----------|
| `DEBUG` | Internal state during development (variable values, branch taken) |
| `INFO` | High-level progress milestones — operators read this |
| `WARNING` | Something unexpected but recoverable. Not for expected situations. |
| `ERROR` | Something failed but the program continues |
| `CRITICAL` | The program cannot continue |

`INFO` is for operators; `DEBUG` is for developers. Don't use `INFO` for per-item detail that floods output.

---

## FFI Conventions

The boundary between languages is where each language's conventions disagree most. These rules apply identically on both sides of the FFI.

| Rule | Mechanism |
|------|-----------|
| Names cross unchanged | `parse_layout` in Python pairs with `parse_layout` in C++. No `parseLayout`, no `_parse_layout_impl` shim. |
| Validation is the calling side's job | The caller validates before crossing. The called side may assert preconditions cheaply but does not re-validate defensively. |
| Errors translate exactly once | C++ exception → Python exception at the pybind11 layer. Python does not wrap pybind11-translated exceptions; original type and message preserved. |
| Absence maps to absence | `Optional[T]` ↔ `std::optional<T>`; `None` ↔ `std::nullopt`. NaN does not appear. Empty collections do not signal failure (use `Optional` or raise). |
| Units survive the trip | Conversion happens at the *outer* boundary (user input, file parsing). Never at the FFI seam — converting at the FFI is a category error. |
| Ownership is explicit | By-value crossings copy. By-reference crossings are non-owning views with documented lifetime. Python does not pass mutable objects expecting C++ to retain them past the call. C++ does not return raw pointers to Python; ownership transfers via `std::unique_ptr` (which pybind11 wraps) or by-value copy. |
| The IR is the contract | Shared data structures (move IR, parsed layouts, plan output) have one schema, one source of truth. A schema change is versioned and requires both sides plus the goldens to move together. |

---

## Naming Vocabulary

Same verb, same operation. The verbs apply to both Python and C++ so names cross the FFI boundary unchanged.

| Verb | Meaning | Example |
|------|---------|---------|
| `parse_*` | String/text → structured data | `parse_config`, `parse_dimension` |
| `format_*` | Structured data → string/text | `format_output`, `format_report` |
| `resolve_*` | Simplify structure, expand references | `resolve_layout`, `resolve_template` |
| `*_to_*` | Convert between typed representations | `model_to_dto`, `ast_to_ir` |
| `validate_*` | Check correctness, raise on failure | `validate_config`, `validate_bounds` |
| `build_*` | Construct complex object from parts | `build_pipeline`, `build_tool_db` |
| `load_*` | Read from disk or external source | `load_config`, `load_template` |
| `write_*` | Emit machine/file output | `write_output`, `write_report` |
| `render_*` | Emit visual/display output | `render_diagram`, `render_html` |
| `expand_*` | Parameterized instantiation | `expand_template`, `expand_macro` |

Predicates and constructor-style helpers:

| Pattern | Returns / Purpose | Example |
|---------|-------------------|---------|
| `is_*` / `has_*` | `bool` | `is_convex`, `has_through_cut` |
| `try_from` / `try_*` | `T \| None` instead of raising. Pairs with [wrapper types](#pattern-wrapper-type-for-preconditions). | `ConvexPolygon.try_from`, `try_parse_dimension` |
| `find_*` | `T \| None` from a search | `find_tool`, `find_intersection` |
| `make_*` | Factory function | `make_default_config`, `make_tool_db` |
| `get_*` | Accessor that cannot fail; precondition is the caller's responsibility | `get_bounds`, `get_active_tool` |

Private helpers use underscore prefix with the same verb conventions: `_handle_*`, `_build_*`, `_validate_*`, `_collect_*` / `_count_*`, `_is_*` / `_has_*`.

---

## Tooling Commitments

The build refuses what the standard says it should. Pre-commit hooks colocate checks with the code; CI is appropriate when the project grows beyond solo work.

| Tool | Configuration |
|------|--------------|
| Linting | `ruff` with project config in `pyproject.toml`. Baseline: `E`, `F`, `W`, `I`, `B`, `UP`, `SIM`. Per-disable requires a comment in `pyproject.toml` or `# noqa: <code>` at the call site. |
| Formatting | `ruff format`. Starter: line length 100, double quotes, default ruff style. Decided once, not relitigated. |
| Type checking | `mypy --strict`. New projects start strict. `# type: ignore[code]` with the specific code (never bare) permitted at boundaries with untyped third-party libraries. |
| Testing | `pytest`. Tests live in `tests/`. Coverage measured but not gated on a percentage. |
| Python version | Project picks 3.12+ for new work. Stated in `pyproject.toml`. Reaching for a 3.13 feature on a 3.12 project is a bug. |

Per-project, not standard-level: ruff disables beyond baseline, line length, test framework choice, build/packaging tool (`uv`, `poetry`, plain `pip` + `pyproject.toml`), dependency management policy.

---

## What This Convention Does Not Require

- **Naming case.** `snake_case` for functions/variables, `PascalCase` for types, `SCREAMING_SNAKE_CASE` for module-level constants. PEP 8 default; don't relitigate.
- **Docstring quotas.** Docstrings only on public API, non-obvious algorithms, and load-bearing assumptions a reader couldn't infer. No docstrings restating the function signature.
- **Metaclass tricks.** Metaclasses, `__init_subclass__`, dynamic class generation — only after at least two concrete cases justify them. The first instance is a function; the second is when you decide whether it's a pattern.
- **`__getattr__` magic.** Dynamic attribute access for things that could be explicit methods or dictionary lookups makes code untraceable. Use only for genuine proxy/forwarding patterns at module boundaries.
- **Decorator stacks > 2 deep.** A decorator can replace a function; two decorators can compose; three or more is a sign the wrapping is doing work that should be a real function call.
- **Premature `Generic[T]`.** Generic type variables are for code that genuinely operates on multiple types. Wait until the second type exists.
- **Monkey-patching.** Don't reach into another module's namespace to replace its functions. Pass behavior in or wrap explicitly.
- **Exotic Python.** Walrus operators in obscure positions, structural pattern matching for things that are obviously dict lookups, descriptor protocol for things that are obviously properties — when the boring construction works, use the boring construction.
- **Comments for what the code does.** Comments only explain *why*, never *what*. If code needs a comment to explain what it does, rename things until it doesn't. No commented-out code, no `# removed` markers — version control remembers.

---

## Values

When the rules above don't address a case, return here.

- **Boring is a feature.** Two language features instead of seven, idiomatic instead of clever, explicit names over compressed ones. The next reader should understand the code without leaving the file.
- **Failure modes are visible.** Errors don't get swallowed. Invalid states are unrepresentable when possible, validated at construction when not. Silent partial output is the worst possible failure mode in any system whose output drives downstream work.
- **Determinism is the default.** Same input, same output. No hidden state, no randomness without explicit seeds, no dependence on dict ordering at runtime, no floating-point platform variance baked into outputs.
- **Defensive at boundaries, trusting inside.** Validate at user input, file parsing, API responses, FFI calls. Trust validated data internally. Construction-time validation converts "I should check this" into "the type system already did."
- **The type checker is your ally.** Type hints on every public signature. Frozen dataclasses for data structures. Exhaustive union dispatch with a final `raise TypeError`. `Enum` for closed sets, `Literal` for inline constraints. `__post_init__` for construction-time validation. `mypy --strict` so the checker catches what the annotations promise.

---
layer: pattern
pattern: compiler
language: python
---

# Compiler pattern — Python conventions

Conventions for projects declaring the Compiler pattern. Extends the global Python coding guidelines in `conventions/global/python.md`.

The Compiler pattern: input AST → IR → output codegen, with the IR as the single canonical intermediate form.

---

## Pipeline architecture

### Strict layer separation

Implements GL-1 (pipelines do not re-enter or escape scope).

```python
# Right
model = parse(raw_input)
validated = validate(model)
output = render(validated)

# Wrong (skipped validation)
def build_and_export(raw_input):
    model = parse(raw_input)
    output = render(model)
    return model
```

### Use an explicit IR as the validation checkpoint

The IR describes *what*, not *how*. Validation happens once. Tests target the IR, not the full pipeline.

### No pass-through of computed data through semantic layers

```python
# Right (semantic layer stays clean)
@dataclass(frozen=True)
class Feature:
    name: str
    position: float

def feature_to_backend(feature: Feature) -> BackendInput:
    offset = compute_offset(feature.position)
    return BackendInput(offset=offset)

# Wrong (implementation detail leaks into semantic)
@dataclass(frozen=True)
class Feature:
    name: str
    computed_offset: float
```

---

## Dependency direction

### Imports flow downward

```
Input/CLI → Parser → IR/Model → Validation → Backend/Output
```

Lower layers must not import from higher layers. Removing a higher layer must not break lower modules' imports.

### Adapter pattern at boundaries

```python
# Right (adapter at boundary)
# model.py
@dataclass(frozen=True)
class Feature:
    style: str  # generic

# adapter.py
from model import Feature
from renderer.types import RenderHint

def feature_to_render_hint(feature: Feature) -> RenderHint:
    return STYLE_MAP[feature.style]

# Wrong (model depends on renderer)
# model.py
from renderer.types import RenderHint

@dataclass(frozen=True)
class Feature:
    render_hint: RenderHint
```
