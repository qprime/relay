# Invariant: The host verifier's purity is a property of the link graph

**Status:** Active | **As-Of:** 2026-08-10 | **Scope:** `host/src/verify/`, `host/CMakeLists.txt`

## Statement

The C++ verifier is split across three CMake targets, and the split is the
enforcement mechanism:

| Target | Links | Contents |
|---|---|---|
| `relay_core` | nothing | `src/io_image.cpp`, `src/json_text.cpp` |
| `relay_verify` | `relay_core` | `src/verify/assertion_parser.cpp`, `src/verify/verifier.cpp` |
| `relay_verify_io` | `relay_core`, nlohmann | `src/verify/trace_reader.cpp`, `src/verify/verdict_writer.cpp` |

`relay_verify` may not link asio, nlohmann, or `relay_host`. Evaluation is a
pure function of `(Trace, span<const string>) → vector<AssertionResult>`: no
socket, no file, no clock, no model client, and no path by which one could
arrive.

Both wire-format directions sit **outside** the boundary, exactly as
`relay/trace_io.py` and `relay/verdict_io.py` sit outside the Python
invariant's. Reading a trace and writing a verdict are I/O; deciding what the
trace says is not.

## Why a sibling to `verification_path_purity` rather than an amendment

[verification_path_purity](verification_path_purity.md) is built on a Python
import allowlist and denylist, and on the specific edge along which a model
client would arrive — `relay.generator.*`. The C++ constraint is a different
mechanism with a different enforcement: the guarantee is checked by what the
linker will accept, not by an AST walk. One document trying to state both in
two languages states neither precisely.

The two are the same claim about the same stage, held by different means. Read
the Python one for *why* verification must be deterministic; this one only says
how that is held on the host.

## Why the split exists at all

The verifier's value is that it is a **second, independently written** answer to
the same question. `tests/test_cross_verifier_agreement.py` points it at
`tests/golden/conveyor_trace.jsonl` — the Python simulator's own trace — so the
trace is held fixed and the verifier is the only variable.

That claim survives only while the C++ side stays a verifier. A verifier that
could read the spec, call out to a service, or consult the runtime would have
inputs the Python one does not, and "two verifiers agree" would stop meaning
"two implementations of one rule agree."

`relay_core` had to be extracted for this: `Cell`/`is_truthy` and
`format_json_double` were compiled into `relay_host`, which links asio, so a
verifier reusing them would have inherited the whole runtime's dependency set.

## What this looks like

1. **`relay_verify` links `relay_core` and nothing else.** `relay_core`
   exposes `include/` and depends on nothing, so `#include <asio.hpp>` or
   `#include <nlohmann/json.hpp>` in `verifier.cpp` fails to resolve at
   compile time rather than passing review.
2. **`format_json_double` and `escape_json_string` are shared, not
   reimplemented.** The trace and the verdict are two documents describing one
   run, and Python's `json` module is normative for both. Two formatters that
   disagreed would spell one value two ways, and no round-trip test on either
   document alone could see it.
3. **Comm-tag-ness is derived from the trace, never from the spec.** Python
   uses `any(name in r.sends for r in trace.records)`; the host does the same.
   A C++ verifier reading `Comm.tags` implements a different rule, and the two
   diverge wherever the spec and the trace disagree.
4. **Selection rules name properties of the trace, not of the container.**
   First-in-list-order is not something a second implementation can be
   independently correct about. Both sides select minimum-by-`(elapsed_ms,
   plc_id)`.
5. **The trace reader rejects rather than coerces.** It faces bytes it did not
   write, so [wire_format_serialization](wire_format_serialization.md) governs
   it: value-level guards, the predicate each field's type calls for, and the
   line number on every error.

## What violates this invariant

- Adding asio, nlohmann, or `relay_host` to `relay_verify`'s link line — for
  any reason, including a convenience include in a test helper.
- Moving `trace_reader.cpp` or `verdict_writer.cpp` into `relay_verify`
  "since they are part of verification." They are the wire format; the whole
  point of the boundary is that it falls between them and the evaluator.
- A `relay_verify` source reading a file, opening a socket, or taking a
  `std::filesystem::path`.
- Passing the resolved spec into `evaluate_assertion` so `CAUSES` can check
  `Comm.tags`. The trace is the sole input.
- Making the C++ verdict authoritative. Python is the oracle; a disagreement
  is a bug to investigate, not a reason to regenerate an artifact.
- Comparing `reason` strings across the two verifiers in a test. That couples
  the C++ implementation to Python's prose, so improving a witness sentence
  breaks the other side for no verification gain.

## What is NOT covered by this invariant

- **`host/src/verify/verify_main.cpp`.** The binary reads argv, opens files,
  and reads the spec's `assertions` array. It is the caller, and the
  invariant constrains what the verifier links, not who invokes it — the same
  carve-out `verification_path_purity` gives `tools/`.
- **`relay_host` itself.** The runtime links asio because it is a runtime.
- **Test code.** `host/tests/` links everything; how tests construct scenarios
  is not constrained.
- **Header visibility.** `relay_core` exposes the whole `include/` tree, so a
  `relay_verify` source can textually include a `relay_host` header. What it
  cannot do is include one that pulls asio or nlohmann, or use a symbol that
  needs `relay_host` at link time. The guarantee is about dependencies
  reached, not about which directory a header sits in.

## Failure mode this prevents

A contributor extending `CAUSES` wants the declared tag list and adds
`#include "relay_host/spec_loader.hpp"`, then adds nlohmann to `relay_verify`
to make it compile. Every test stays green — the conveyor spec's `Comm.tags`
and its trace's `sends` agree, so the two tag-derivation rules give the same
answer for every scenario in the repo.

Later a spec declares a tag no PLC ever sends, or the generator stops emitting
one it declares. The C++ verifier now resolves a name the Python verifier
treats as an ordinary signal. The two verdicts differ, and the disagreement
looks like a runtime bug rather than what it is — two verifiers implementing
two different rules. The cross-verifier test, whose entire value is that the
verifier is the only variable, has quietly stopped being that test.

## Examples in this codebase

- **The link lines**: `relay_verify` in
  [host/CMakeLists.txt](../../host/CMakeLists.txt), carrying only `relay_core`.
- **The mechanical check**:
  [tests/test_host_verify_purity.py](../../tests/test_host_verify_purity.py)
  parses the `relay_verify` sources' includes and the CMake link line, the way
  `TestVerdictIOPurity` parses `relay/verdict_io.py`.
- **The claim it protects**:
  [tests/test_cross_verifier_agreement.py](../../tests/test_cross_verifier_agreement.py),
  which runs both verifiers over `tests/golden/conveyor_trace.jsonl`.

## Related

- [verification_path_purity.md](verification_path_purity.md) — the same claim,
  held on the Python side by an import allowlist
- [wire_format_serialization.md](wire_format_serialization.md) — governs the
  trace reader and the verdict writer, which sit outside this boundary
- `host/README.md` — the verdict-equality contract the host is judged by
