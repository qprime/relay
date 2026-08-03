# QPrime C++ Coding Convention

**Status:** Standard | **Version:** 2.0 | **Scope:** all C++ in QPrime / TenneCNC projects.

This document exists so an agent generating C++ writes code that compiles, passes review, and survives the FFI on the first try. Read the Quick Lookup table first; load the rest by reference when a trigger sends you there. Loaded terms (*wrapper type*, *result type*, *trap*) are deliberate — grep for them.

---

## Quick Lookup

When you're about to write... go to...

| Situation | Section |
|-----------|---------|
| Function with structural precondition (convex, sorted, non-empty) | [Pattern: wrapper type for preconditions](#pattern-wrapper-type-for-preconditions) |
| Function that may fail | [Pattern: failure mechanism](#pattern-failure-mechanism) |
| Choosing how to pass a parameter | [Pattern: pass-by convention](#pattern-pass-by-convention) |
| Editing a real-time loop, audio callback, ISR | [Pattern: real-time loops](#pattern-real-time-loops) |
| Editing or adding a coroutine | [Pattern: coroutines](#pattern-coroutines) |
| Adding an import to a low-level module | [Pattern: dependency direction](#pattern-dependency-direction) |
| `std::string kind` field, `enum kind` + if/elif | [Trap: stringly-typed dispatch](#trap-stringly-typed-dispatch) |
| `(T* ptr, size_t len)` parameter | [Trap: pointer-and-length pair](#trap-pointer-and-length-pair) |
| NaN as "no value" | [Trap: NaN sentinel](#trap-nan-sentinel) |
| `std::shared_ptr<T>` as the heap-allocation default | [Trap: reflexive shared_ptr](#trap-reflexive-shared_ptr) |
| `std::mutex` member to make a class "thread-safe" | [Trap: cargo-culted mutex](#trap-cargo-culted-mutex) |
| `auto` everywhere | [Trap: auto by default](#trap-auto-by-default) |
| `noexcept` on every function | [Trap: blanket noexcept](#trap-blanket-noexcept) |
| `template<...>` as the first reach for parameterization | [Trap: premature template](#trap-premature-template) |
| Inheritance hierarchy for variant types | [Trap: inheritance for variation](#trap-inheritance-for-variation) |
| Catching exceptions to convert to error codes | [Trap: mid-stack exception translation](#trap-mid-stack-exception-translation) |
| `(void)param;` to silence a warning | [Trap: void-cast unused param](#trap-void-cast-unused-param) |
| Stub function returning `{}` | [Trap: empty-stub public function](#trap-empty-stub-public-function) |
| Inline numeric literal in a check | [Trap: magic number](#trap-magic-number) |
| Storing `std::span` as a member | [Trap: stored span](#trap-stored-span) |
| Two functions sharing >half their bodies | [Trap: parallel near-duplicates](#trap-parallel-near-duplicates) |
| Editing pybind11 binding or schema shared with Python | [FFI Conventions](#ffi-conventions) |
| Adding logging | [Logging](#logging) |
| Adding a test | [Testing](#testing) |
| Choosing a verb for a new function | [Naming Vocabulary](#naming-vocabulary) |
| Code in `cam/native/cpp/` for the first time | [Tooling Commitments](#tooling-commitments) |

---

## Patterns

### Pattern: Wrapper Type for Preconditions

Functions with a structural precondition (convex polygon, sorted range, non-empty buffer, monotonic sequence, oriented loop with known winding) take a *wrapper type* that proves the precondition. The check happens once, at the boundary, not inside every algorithm that wants to assume it.

```cpp
class ConvexPolygon {
 public:
  static std::optional<ConvexPolygon> try_from(Polygon p);
  const Polygon& points() const;
 private:
  explicit ConvexPolygon(Polygon p);
  Polygon points_;
};

Polygon inset(const ConvexPolygon& poly, double offset);
```

`inset`'s signature *proves* the precondition. A non-convex polygon cannot reach `inset` without going through `try_from`. The wrapper is the C++ expression of "preconditions named in the type" — it pairs with `try_*` factories returning `std::optional` (see [Naming Vocabulary](#naming-vocabulary)).

Apply when the precondition is *structural*, not scalar. A scalar precondition (positive width, non-empty string) lives on the type that already owns the field.

### Pattern: Failure Mechanism

| Mode | Use When |
|------|----------|
| `std::optional<T>` | Absence is the only failure mode |
| `std::expected<T, E>` | Failure carries information the caller needs to act on |
| Exception | Genuinely exceptional condition: allocation failure, invariant violation, unrecoverable corruption |
| `assert` | "This can't happen" precondition that upstream validation should already guarantee. Sparingly — frequent asserts mean a wrapper type is missing |
| Silent partial output | Never. |

`std::expected<T, E>` is the default for routine fallible operations; reach for `std::optional<T>` only when absence is the *sole* failure mode and the caller has no use for diagnostic information.

**Error message format.** Every constructed error message — exception, `std::expected` payload, log message, structured warning — includes four parts:

1. **What failed** — class, function, or subsystem
2. **What field** — specific parameter or invariant
3. **What constraint** — the rule that was broken
4. **Actual value** — what was received

```cpp
throw std::invalid_argument("SheetConfig: width_mm must be > 0, got -3.5");
```

**Failure semantics by layer.**

| Layer | On Failure | Mechanism |
|-------|-----------|-----------|
| FFI boundary | Translate to host language | C++ exception → Python exception via pybind11; never let an exception cross unhandled |
| Module public API | Return result type | `std::expected<T, E>` recoverable; throw for invariant violations only |
| Internal helpers | Trust contracts | Validated input assumed; assert preconditions if defensible cheaply |
| Real-time loop | Log + continue | Errors recorded in trace structure, surfaced at scan boundary, never thrown |
| Real-time loop boundary | Inspect trace | Caller examines accumulated errors and decides whether to halt |

Failures become less fatal as you move outward. Parsers are strict; orchestrators are lenient about per-item failures and strict about safety constraints.

**Exceptions policy.**

- Permitted at module boundaries and for genuinely exceptional conditions.
- Forbidden in real-time loops and any code path with hard latency requirements.
- Do not cross FFI boundaries. They translate to host-language errors at the pybind11 layer.
- Routine validation failures use result types, not exceptions.
- `-fno-exceptions` is permitted per-module when latency, binary size, or FFI requirements justify it; the module's top-level header states this explicitly.
- A function that genuinely cannot throw is marked `noexcept`. Otherwise it is not. See [Trap: blanket noexcept](#trap-blanket-noexcept).

### Pattern: Pass-By Convention

| Pass by | When |
|---------|------|
| Value | Small types; types you'll modify locally; types you'll move from |
| `const T&` | Large types you'll only read |
| `T&` | Out-parameters (rare; prefer return values or struct returns) |
| Pointer | Null is a meaningful value; otherwise use reference |
| `std::span<const T>` | Sequence read |
| `std::span<T>` | Sequence write |

### Pattern: Real-Time Loops

Scan loops, audio callbacks, ISRs. Different rules apply:

| Concern | Rule |
|---------|------|
| Exceptions | Forbidden — non-deterministic timing |
| Allocation | Pre-allocate; `vector::push_back`, `string` operations that may reallocate, anything calling `malloc` are scrutinized |
| Errors | Log into a pre-allocated trace structure; surface at scan boundary; never throw |
| Logging | Trace structure, not a runtime logger — formatting and lock contention are too expensive |

### Pattern: Coroutines

| Concern | Rule |
|---------|------|
| Reference parameters into a coroutine that may suspend | Forbidden — the reference dangles if the caller's frame is destroyed before resumption. Pass by value. |
| Lambda captures into coroutines | By value, unless the lambda's lifetime is provably bounded by the captured object's |
| Awaitable lifetime | Awaitables are non-owning by default; if an awaitable needs to outlive the awaiting frame, that's an explicit ownership question |
| Deep `co_await` chains (>2–3 deep) | Use symmetric transfer (return `coroutine_handle<>` from `await_suspend`) to prevent stack growth |

### Pattern: Dependency Direction

Imports flow downward:

```
Input/CLI  →  Parser  →  IR/Model  →  Validation  →  Backend/Output
```

Each layer may include from layers to its right, never to its left.

| Trigger | Action |
|---------|--------|
| Lower layer needs a higher layer's type | Introduce an adapter at the boundary; do not pull the higher layer's headers down. |
| Wrapper type used by multiple layers | Lives at the layer that owns the precondition, not the layer that consumes the wrapped value. |
| Ambiguity check | Delete the higher-level module mentally — do lower-level modules still compile? If not, dependency is inverted. |

---

## Traps

Each trap names a pattern AI generation produces by default and the rule that contradicts it.

### Trap: stringly-typed dispatch

A struct with a `std::string kind` field plus optional payload fields is a compile-time-unchecked tagged union. So is an `enum class` paired with an if/elif chain — the enum is half the fix.

**Use:** `std::variant` for the type, `std::visit` with a lambda overload set for dispatch.

```cpp
struct Comment { std::string text; };
struct SetRpm  { double rpm; };
struct Rapid   { double x, y, z; };
struct Cut     { std::optional<double> x, y, z, feed; };

using Move = std::variant<Comment, SetRpm, Rapid, Cut>;

auto handle(const Move& m) {
  return std::visit([](auto&& move) { /* per-type body */ }, m);
}
```

Adding a new alternative to the variant turns "forgot to handle this somewhere" into a compile error.

### Trap: inheritance for variation

Inheritance is for sharing *implementation*, not for representing *variation*. A `class Move { virtual ~Move(); }` with `class Rapid : public Move` and `class Cut : public Move` is a v-table where a `std::variant` belongs.

**Use:** `std::variant` + `std::visit`. Inherit only when subclasses share implementation that the base class provides concretely.

### Trap: NaN sentinel

NaN is *invalid number*, not *no number*. Conflating the two means real NaN bugs (degenerate geometry, division by zero, uninitialized arithmetic) are indistinguishable from intentional absence.

**Use:** `std::optional<T>` for absence. NaN is a bug to investigate, not a value with meaning. FFI bindings map `None` / `null` ↔ `std::nullopt`.

### Trap: pointer-and-length pair

A function taking `(const T* ptr, size_t len)` puts the lifetime contract in the comments and the bounds check on every caller. The type system is willing to do both for you.

**Use:** `std::span<const T>` for read; `std::span<T>` for write.

```cpp
void process(std::span<const Vec2> path);
```

**At `extern "C"` boundaries** the foreign signature dictates `(T*, size_t)`. Convert to a span on entry and don't refer to the raw pointer again:

```cpp
extern "C" int process_buffer(const Vec2* data, size_t length) {
  const std::span<const Vec2> path(data, length);
  // body uses path
}
```

See also: [Trap: stored span](#trap-stored-span).

### Trap: stored span

A `std::span<T>` is a non-owning view. Storing it as a class member ties the class's lifetime to the data the span points at — usually a use-after-free waiting to happen.

**Use:** if the class needs to retain the data, store `std::vector<T>`. If it only needs a view for the duration of one call, take the span as a parameter, not a member.

### Trap: reflexive shared_ptr

`std::shared_ptr<T>` is for *genuinely shared* ownership — multiple independent owners with no clear primary. Reaching for it because "I'm not sure who owns this" hides the ownership question and pays for an atomic refcount the design doesn't need.

**Use:**
- `std::unique_ptr<T>` for owned heap allocation; transfer by move.
- Raw pointer or reference for non-owning views (with documented lifetime if non-obvious).
- `std::shared_ptr<T>` only when ownership is actually shared.

### Trap: cargo-culted mutex

Adding `std::mutex` to a class without a documented threading model is cosplay, not thread-safety. The default for any type is *single-threaded by contract* — concurrent access is the caller's problem.

**Use:** leave the class single-threaded. Document the threading model only when concurrency is actually introduced. When it is:

| Need | Mechanism |
|------|-----------|
| Shared primitive (counter, flag, single-pointer handoff) | `std::atomic<T>` |
| Compound shared state | `std::mutex` + `std::lock_guard` / `std::unique_lock` |
| Read-heavy access (measured, not assumed) | `std::shared_mutex` |
| Wait/notify | `std::latch` / `std::barrier` (C++20) preferred over raw condition variable |
| One-time init | `std::call_once` |
| Pure functions, immutable data | nothing — no synchronization needed |

Forbidden: `volatile` for synchronization, double-checked locking without atomics, `sleep_for` to wait for a condition, raw `std::thread` ownership scattered through application code.

If a module's concurrency model isn't obvious from its API, document it in one or two sentences at the top of the header.

No thread-local globals. If thread-local state is genuinely required, it lives in an explicit per-thread context object passed into the functions that need it.

### Trap: auto by default

`auto` hides the type from the reader. Use it when the type is obvious from the right-hand side (`auto it = container.begin()`, `auto p = std::make_unique<Foo>()`). Don't use it when the type is the load-bearing fact (`auto result = compute_thing();` — what's the type of `result`?).

### Trap: blanket noexcept

`noexcept` is a contract. On a function that transitively calls anything that might throw, `noexcept` converts any escaped exception into `std::terminate` — i.e. a crash. "It's free" is wrong; it's a strong claim that has to be true.

**Use:** `noexcept` on functions that genuinely cannot throw (move constructors of trivially-movable types, swap, pure arithmetic on built-ins). Don't decorate functions reflexively.

### Trap: premature template

Templates are for genuine generic code. A function with two concrete callers is not generic — it's two functions. Reach for templates when the alternative is genuinely worse, not as a default.

**Use:** start with a concrete type. Templatize when a third caller forces it.

### Trap: mid-stack exception translation

Translating exceptions to error codes (or one exception type to another) at every layer produces noise that hides real handling. The throw-as-control-flow pattern is also forbidden.

**Use:** exceptions for genuinely exceptional conditions (allocation failure, invariant violation, FFI-translated host-language exceptions). Result types for routine fallible operations. Translate exactly once: at the FFI boundary, into the host language's mechanism. See [Pattern: failure mechanism](#pattern-failure-mechanism) and [FFI Conventions](#ffi-conventions).

### Trap: void-cast unused param

`(void)param;` to silence an unused-parameter warning is a marker that the parameter shouldn't have been there.

**Allowed:** `(void)param;` on a virtual override or interface implementation where the parameter is mandated by the signature.
**Not allowed:** on a leaf function — delete the parameter.

### Trap: empty-stub public function

A function declared in a public header that returns `{}` is indistinguishable from a real function that produced an empty result.

**Use:** delete the function until it's implemented. If a caller needs the symbol to exist before the implementation lands, the function is `[[noreturn]]` and throws `std::logic_error("not implemented: <name>")`. The pybind11 binding for an unimplemented function is omitted entirely.

### Trap: magic number

Inline numeric literals in geometry, timing, and limit checks are unmaintainable and produce inconsistencies when the same value reappears.

**Use:** `constexpr` (or `inline constexpr` in a header) at the top of the file, or in a dedicated constants header for cross-module values.

```cpp
constexpr double kMinMarginMm = 10.0;
if (margin < kMinMarginMm) { ... }
```

Trivially obvious literals (`0`, `1`, array indices, loop bounds tied to a local container) don't need names.

### Trap: parallel near-duplicates

Two functions sharing more than half their bodies, where a future change would need to be made in both places, drift silently — a bug fix in one is forgotten in the other.

**Use:** collapse into one function with an explicit options struct.

```cpp
struct PlanOpts {
  enum class Strategy { A, B } strategy;
  bool include_finishing_pass;
};
Result plan(const Input&, const PlanOpts&);
```

The test is "would a future change need to co-evolve in both places." Accidental similarity that wouldn't co-evolve stays separate.

---

## FFI Conventions

The boundary between languages is where each language's conventions disagree most. These rules apply identically on both sides of the FFI.

| Rule | Mechanism |
|------|-----------|
| Names cross unchanged | `parse_layout` in Python pairs with `parse_layout` in C++. No `parseLayout`, `parse_layout_t`, or `_parse_layout_impl`. |
| Validation is the calling side's job | Caller validates before crossing. The called side may assert preconditions cheaply but does not re-validate defensively. |
| Errors translate exactly once | C++ exception → Python exception at the pybind11 layer. C++ does not catch to translate mid-stack. Python does not wrap pybind11-translated exceptions. Original type and message preserved. |
| Absence maps to absence | `std::optional<T>` ↔ `Optional[T]`; `std::nullopt` ↔ `None`. NaN does not appear. Empty collections do not signal failure (use the result type). |
| Units survive the trip | Conversion happens at the *outer* boundary (user input, file parsing). Never at the FFI seam — converting at the FFI is a category error. |
| Ownership is explicit | By-value crossings copy. By-reference crossings are non-owning views with documented lifetime. C++ does not return raw pointers to host-language code; ownership transfers via `std::unique_ptr` (which pybind11 handles) or by-value copy. Python does not pass mutable objects expecting C++ to retain them past the call. |
| The IR is the contract | Shared data structures (move IR, parsed layouts, plan output) have one schema, one source of truth. A schema change is versioned and requires both sides plus the goldens to move together. |

---

## Testing

| Rule | Mechanism |
|------|-----------|
| Test at the level the logic lives | Unit tests target the function or class, not the full pipeline. Pipeline tests are integration only. |
| Don't test the language | No tests for `std::optional` returning `nullopt` when default-constructed, or `constexpr` evaluating at compile time. Test what *your code* adds. |
| Round-trip tests assert semantic equivalence | `parse(serialize(model)) == model`. Whitespace, key order, equivalent representations may legitimately differ. |
| No duplicate coverage | Two tests asserting the same behavior over the same input is a defect. Check existing tests before adding a new file. |
| Use the framework | Catch2, GoogleTest, doctest, per project. No `int main()` runners, no PASS/FAIL prints, no manual reporting bypassing the framework. |
| Golden-tested IR | Any computation producing structured output (toolpaths, plans, schedules, traces, generated code) has a golden-tested IR. Every change is either (a) no golden change — the change is a refactor, or (b) deliberate snapshot regeneration with the diff explained in the commit message. Adding a new IR alternative is versioned: define, expose across the FFI, document, regenerate goldens — in that order. |

---

## Logging

| Rule | Mechanism |
|------|-----------|
| No `std::cout` / `printf` in library code | They belong in CLI entry points. A `printf` in a deep helper spams stdout every run. |
| Use a structured logger | spdlog, glog, or the project's chosen library. |
| Real-time loops use trace structures | A runtime logger's lock contention and formatting cost are too expensive. Surface diagnostics at the scan boundary. |

Level discipline:

| Level | Use When |
|-------|----------|
| `TRACE` / `DEBUG` | Internal state during development (variable values, branch taken) |
| `INFO` | High-level progress milestones — operators read this |
| `WARN` | Something unexpected but recoverable. Not for expected situations. |
| `ERROR` | Something failed but the program continues |
| `FATAL` / `CRITICAL` | The program cannot continue |

---

## Naming Vocabulary

Same verb, same operation. The verbs apply to both Python and C++ so names cross the FFI boundary unchanged.

| Verb | Meaning | Example |
|------|---------|---------|
| `parse_*` | Text/bytes → structured data | `parse_config`, `parse_dimension` |
| `format_*` | Structured data → text/bytes | `format_output`, `format_report` |
| `resolve_*` | Simplify structure, expand references | `resolve_layout`, `resolve_template` |
| `*_to_*` | Convert between typed representations | `model_to_dto`, `ast_to_ir` |
| `validate_*` | Check correctness; throw or return error on failure | `validate_config`, `validate_bounds` |
| `build_*` | Construct complex object from parts | `build_pipeline`, `build_tool_db` |
| `load_*` | Read from disk or external source | `load_config`, `load_template` |
| `write_*` | Emit machine/file output | `write_output`, `write_report` |
| `render_*` | Emit visual/display output | `render_diagram`, `render_html` |
| `expand_*` | Parameterized instantiation | `expand_template`, `expand_macro` |
| `plan_*` | Compute execution sequence | `plan_pocket`, `plan_profile` |

Predicates and accessors:

| Pattern | Returns |
|---------|---------|
| `is_*` / `has_*` | `bool` |
| `try_*` | `std::optional<T>` or `std::expected<T, E>`. Pairs with [wrapper types](#pattern-wrapper-type-for-preconditions) — `ConvexPolygon::try_from` is canonical. |
| `get_*` | Accessor that cannot fail; precondition is the caller's responsibility |
| `find_*` | `std::optional<T>` or iterator |
| `make_*` | Construct a value (`std::make_unique`, `std::make_shared` style) |

---

## Tooling Commitments

The build refuses what the standard says it should. Pre-commit hooks colocate checks with the code; CI is appropriate when the project grows beyond solo work.

| Tool | Configuration |
|------|--------------|
| Warnings | `-Wall -Wextra -Wpedantic -Werror`. Per-disable requires a comment. |
| `-Wconversion` | Recommended on new projects; judgment on existing code. |
| Sanitizers | UBSan + ASan in at least one configuration. TSan when concurrency is introduced. Findings block merge. |
| Static analysis | `clang-tidy` with `bugprone-*`, `cert-*`, `cppcoreguidelines-*`, `performance-*`, `readability-*`. Project-level disables in `.clang-tidy` with a one-line comment per disable. |
| Formatting | `clang-format` per project. Starter: `BasedOnStyle: Google`, `IndentWidth: 4`, `ColumnLimit: 100`. |
| Build system | CMake default for cross-platform; alternatives permitted with reason. |
| C++ standard | Project picks C++20 or C++23 for new work. Stated in top-level build config. Reaching for a C++26 feature on a C++20 project is a bug. |

Per-project, not standard-level: clang-tidy disables, clang-format details beyond the baseline, build system, library structure, test framework.

---

## What This Convention Does Not Require

- **Naming case.** `snake_case` for functions, `PascalCase` for types, `SCREAMING_SNAKE_CASE` for constants. Pick once at project level. Don't relitigate.
- **Header zealotry.** `#pragma once` is fine. Include-what-you-use is a goal, not a gate.
- **Comment quotas.** Comments only for non-obvious geometry, non-obvious mathematical identities, and load-bearing assumptions a reader couldn't infer from the code. No docstrings, no running prose explaining the next three lines.
- **Template metaprogramming.** See [Trap: premature template](#trap-premature-template).
- **Premature abstraction.** Inheritance hierarchies, CRTP, policy classes — only after at least two concrete cases justify them. The first instance is a function; the second is when you decide whether it's a pattern.
- **Exotic C++.** Modules, contracts, reflection — until tooling supports them broadly.

---

## Values

When the rules above don't address a case, return here.

- **Boring is a feature.** Two language features instead of seven, idiomatic instead of clever, explicit names over compressed ones. The next reader should understand the code without leaving the file.
- **Failure modes are visible.** Errors don't get swallowed. Invalid states are unrepresentable when possible, asserted when not. Silent wrong-answers are the worst possible failure mode in a system whose output drives physical action.
- **Ownership is obvious.** Who owns this memory, this resource, this lifetime — answerable in under five seconds by anyone reading the code. RAII by default. Raw pointers only as non-owning observers with documented lifetimes.
- **Defensive at boundaries, trusting inside.** Validate at function parameters from outside the module, at deserialization, at FFI. Trust internal invariants once established. Wrapper types convert "I should check this" into "the type system already did."
- **Determinism is the default.** Same input, same output. Undefined behavior, signed overflow, unordered-container iteration order, link order, thread interleaving, uninitialized memory — none may leak into output.
- **The compiler is your ally.** Strong types over primitives where they matter. `enum class` over loose enums. `[[nodiscard]]` on functions whose return value matters. `constexpr` where possible. `noexcept` where genuinely true. `std::variant` + `std::visit` for compile-time exhaustiveness.
