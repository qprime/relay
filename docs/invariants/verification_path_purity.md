# Invariant: Verification path is a closed import set

**Status:** Active | **As-Of:** 2026-04-21 | **Scope:** `relay/verify/`

## Statement

`relay/verify/*` may import only Python standard library, `relay.runtime.clock`,
and `relay.runtime.plc` (the latter solely for the `IOImage` type, under
`TYPE_CHECKING`). It may NOT import any LLM client, network library, file or
process I/O, or any other module of `relay/` whose transitive dependencies
include such things.

The verification path — assertion evaluation against the trace log — is a
pure function of `(TraceLog, list[assertion_string]) → list[AssertionResult]`.
No call into the network, the model, the filesystem, the clock, the
environment, or the rest of the framework.

## Why

The framework's central claim is that simulation results are *verified*, not
plausible. That claim collapses if any part of the verification path is
non-deterministic, non-reproducible, or judgment-based. An LLM-judges-LLM
loop produces consensus, not truth. A network call introduces flakiness. A
file read introduces hidden inputs. An import of `relay.generator.*`
transitively pulls in `anthropic` and exposes the verifier to model output
being treated as ground truth.

Tests cannot catch this kind of regression. An LLM-powered assertion that
returns `True` still passes — the test result is exactly as expected.
The corruption is invisible from the test result alone; it must be prevented
at the import boundary.

## What this looks like

1. **Allowlist, enforced mechanically.** `relay/verify/*` may import:
   - Python stdlib (`re`, `dataclasses`, `typing`, `pathlib`, etc.)
   - `relay.runtime.clock` (for `SimClock` type)
   - `relay.runtime.plc` (under `TYPE_CHECKING` only, for `IOImage` type)
2. **Denylist, exhaustive at the obvious tier:** `anthropic`, `openai`,
   `requests`, `httpx`, `urllib`, `http`, `socket`, `subprocess`, `os.system`,
   `relay.generator.*`, `relay.spec.*`, `relay.plant.*`, `relay.runtime.comm.*`,
   `relay.st.*`. None of these may appear in `relay/verify/`.
3. **Assertions are pure Python.** New assertion forms are added by extending
   the regex/grammar in `relay/verify/assertions.py` and the evaluator
   functions. The grammar may not include LLM calls, prompts, or external
   lookups.
4. **The trace log is the sole input.** Verification reads `TraceLog.records`
   and the assertion strings. It does not read the spec, the ST source, the
   plant config, or the generator's prior outputs.

## What violates this invariant

- `import anthropic` (or any other model client) anywhere in `relay/verify/`.
- A "smarter" assertion that calls an LLM to judge whether the trace
  "satisfies the user's intent."
- Reading a configuration file, environment variable, or external resource
  during assertion evaluation.
- Importing `relay.generator.*` for "convenience access" to the spec — that
  pulls anthropic in transitively even if the verifier never calls it.
- An assertion that consults an external service (a tracing backend, a
  metrics store, a remote rule engine).
- Caching assertion results to disk between runs in a way the next run
  reads — that introduces hidden state across runs.

## What is NOT covered by this invariant

- **Test code.** `tests/` may import whatever it needs, including the
  generator, to set up scenarios. The verification path under test is
  `relay/verify/`; how tests construct scenarios is not constrained.
- **Trace construction.** Building a `TraceLog` involves the full runtime
  (which imports plenty). The invariant applies to the verifier reading
  the trace, not to the runtime producing it.
- **Performance instrumentation.** A debug print statement is fine. The
  rule is about correctness inputs, not observability.

## Failure mode this prevents

A contributor adds `EVENTUALLY_LOOKS_REASONABLE(<state>)` as an assertion
form, implemented by sending the trace to a model and asking "did this
satisfy the user's intent?" Tests pass — the model agrees with the existing
expected verdicts. CI is green for weeks.

Then a real bug ships: the generated ST emits a signal that *looks* correct
in trace summaries but actually violates a timing constraint. The
LLM-powered assertion approves it. The deterministic verifier would have
caught it because PRECEDES would have failed. But the deterministic check
was deprecated in favor of the "smarter" one, and the framework's
verification guarantee is now the LLM's opinion.

The whole point of `verify/` is to be the place that doesn't lie. Once an
LLM is in there, it is no longer that place.

## Examples in this codebase

- **`relay/verify/assertions.py`** ([relay/verify/assertions.py](../../relay/verify/assertions.py))
  — imports `re`, `dataclasses`, and `relay.verify.trace`. Nothing else.
- **`relay/verify/trace.py`** ([relay/verify/trace.py](../../relay/verify/trace.py))
  — imports `dataclasses`, `typing`, and `relay.runtime.clock`. `IOImage`
  imported under `TYPE_CHECKING` only.

## Enforcement (suggested mechanical check)

A pre-commit hook or test that walks `relay/verify/*.py`, parses imports,
and fails on anything outside the allowlist. The allowlist is short and
stable; the cost of maintenance is near zero compared to the cost of a
silent regression.

## Related

- CLAUDE.md `## Don't` — "Put any LLM call in the verification path" (the
  local form of this invariant)
- CLAUDE.md `Verification-Determinism` capability — "An LLM-judges-LLM loop
  produces plausible results, not verified ones"
