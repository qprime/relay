# Invariant: Wire formats guard both directions with the same predicate

**Status:** Active | **As-Of:** 2026-08-05 | **Scope:** every module that serializes data read back as input to a decision — today `relay/trace_io.py`, `relay/verdict_io.py`, `relay/generator/trigger_io.py`

## Statement

A module whose output is read back as *input to a decision* is a wire format,
and every wire format in relay has the same shape:

1. **Private `_check_*` helpers** validate one field each, raising `TypeError`
   for a wrong type and `ValueError` for a value JSON cannot represent
   portably. Every message names the offending field.
2. **Both directions call the same predicate.** `*_to_dict` and `*_from_dict`
   validate the same fields with the same helpers. Not a looser check on load
   — the same one.
3. **Load-side guards are value-level, not typed reconstruction.** They
   validate raw JSON values and return plain data. They do not import the
   downstream type in order to reconstruct it.
4. **`load_jsonl` / `load_json` wrap `KeyError`, `TypeError`, and `ValueError`**
   in a message carrying the position of the offending entry — the file line
   number for JSONL, the `results[i]` index for JSON.
5. **`sort_keys=True` on every `json.dumps`**, so output bytes depend on
   content and not on dict insertion order.
6. **Streams only.** `TextIO` in and out. No `Path`, no `open()`, no default
   filename — the caller owns the file.

Per-module `indent` is a formatting choice, not part of the invariant:
`verdict_io` writes one indented document, the JSONL modules write one compact
line per record.

## Why

Three modules independently converged on this shape. Three instances is a
convention, and a convention nobody wrote down is one the next contributor has
to infer from whichever instance they happen to read first.

That is not hypothetical. The #10 review found `trigger_io` guarding its dump
path and leaving its load path entirely open. The implementation had mirrored
`trace_io` faithfully — but "mirror the precedent" cannot answer *which side of
the boundary the guard applies to*, because the precedent was never stated. A
hand-edited `triggers.jsonl` — the module's whole stated purpose — loaded into
IR that compiled to malformed ST: `mode: "toggled"` fell through the mode
dispatch in `relay/generator/behavior.py` into the pulse branch and emitted
`PT := T#Nonems`. The format's `duration_ms` rule exists to prevent exactly
that, and it was reached from the unguarded side.

The load path is the one that matters most, and it is the one that gets
skipped. A wire format exists to be read by something that did not just write
it: the C++ host writes a trace the Python verifier reads; a hand-edited
`triggers.jsonl` is `trigger_io`'s reason to exist. The dump side sees values
that came from relay's own typed objects. The load side sees bytes from a
foreign writer, a text editor, or a corrupted file. Guarding only dump protects
the trustworthy direction and leaves the untrusted one open.

"Same predicate on both sides" is what preserves the round-trip property. A
looser load check admits values the dump side would reject, so the set of
loadable documents drifts away from the set of writable ones, and the format
quietly becomes two formats.

## What this looks like

1. **`relay/generator/trigger_io.py` is the reference implementation.**
   `_check_trigger` runs from `trigger_to_dict` and from `trigger_from_dict`,
   over the same fields, with the same `_check_str` / `_check_int` helpers and
   the same enum membership tests. `load_jsonl` wraps `KeyError` naming the
   missing key and `(TypeError, ValueError)` as "line N has an unreadable
   field."
2. **`relay/trace_io.py`** guards every field of a `ScanRecord` from
   `record_to_dict` and from `record_from_dict`, each with the predicate that
   field's type actually calls for:
   - `io_snapshot`, `outputs`, and receipt `value` → `_check_values`, which
     rejects any type outside `(bool, int, float)` and any non-finite float.
   - `sends` and receipt `seq` → `_check_counters`, which rejects `bool` as
     well as non-`int`. Send counters are integers; the signal-value predicate
     is the wrong one for them, because it admits `True` and `0.9` where
     `int()` would then silently truncate them to `1` and `0`.
   - `plc_id` → `_check_str`.
   - `io_snapshot`, `outputs`, `sends`, and `recvs` are each checked to be a
     JSON object before iteration, so a non-object raises `TypeError` through
     the position-carrying wrapper rather than `AttributeError` past it.

   `tick` and `elapsed_ms` remain symmetric `int()` / `float()` conversions on
   load — a known weaker spot, tolerated because it is symmetric and a corrupt
   clock surfaces as a divergence scan rather than a confident wrong verdict.
3. **`relay/verdict_io.py`** guards all four fields from both directions:
   `assertion` and `reason` are `str`, `passed` is `bool`, `observed_gap_ms`
   is a finite number or `None`.
4. **`passed` is rejected, never coerced.** `bool(data["passed"])` turns
   `"yes"` into `True`, `"false"` into `True`, and `[]` into `False`. A corrupt
   verdict file would not fail to load; it would load with an inverted
   pass/fail. Loud rejection is the only acceptable behavior for a field whose
   whole content is a verdict.
5. **Load returns plain data.** `verdict_from_dict` returns a `dict`, not an
   `AssertionResult`. Reconstructing the typed value would require importing
   `relay/verify/`, which
   [verification_path_purity](verification_path_purity.md) and
   [pipeline_direction_imports](pipeline_direction_imports.md) both forbid.

## What violates this invariant

- A `*_from_dict` that reads a field straight out of `data` when the
  corresponding `*_to_dict` runs a `_check_*` on it.
- A load-side check looser than its dump-side twin — accepting `int` where the
  dump side requires `bool`, or skipping the finiteness test that dump applies.
- Reusing a neighbouring field's predicate because it is the one already
  imported. "Both sides call *a* guard" is not the rule; both sides call the
  *same* guard, and it has to be the right one for that field. `sends` guarded
  by the signal-value predicate passes `True` and `0.9` on the dump side, which
  `int()` then truncates on load — asymmetry reintroduced underneath a guard
  that looked present.
- `bool(...)`, `int(...)`, or `str(...)` used as a load-path guard. Coercion is
  not validation: it always succeeds, so it converts a corrupt document into a
  plausible one.
- A guard that reconstructs the downstream type to validate it — importing
  `AssertionResult` into `verdict_io`, or a spec dataclass into a loader — even
  when the resulting signature is more precise.
- `json.dumps` without `sort_keys=True` in a wire-format module. Output bytes
  then depend on dict insertion order, and golden-file comparison stops meaning
  anything.
- A `load_*` that lets a `TypeError` or `ValueError` escape without the line
  number or index. "expected str" without a position is not actionable against
  a thousand-line trace.
- A wire-format function taking a `Path` or calling `open()`. Streams keep the
  module testable from a `StringIO` and keep file-location policy with the
  caller.

## What is NOT covered by this invariant

- **`tools/expectations.py`.** It writes `observed_gap_ms` into the
  expectations artifact through a direct `json.dumps`, bypassing `verdict_io`,
  and that is correct. The expectations artifact is a **regenerable fixture**,
  not an input: `tests/test_expectations.py` rebuilds it from the spec and
  diffs it against the committed copy, and `tools/regenerate_expectations.py`
  rebuilds every one from `specs/*.yaml`. A corrupt field there does not load
  wrong — it fails a diff, loudly, in CI. Nothing downstream consumes it as
  input; it *is* the expected value. `tests/test_host_satisfies_expectations.py`
  reading it with a bare `json.loads` is correct for what it is.

  The scope test is **failure semantics, not file location**: *can a corrupt
  value here produce a confidently wrong result, or does it only fail a
  comparison?* Guards buy something in the first case and nothing in the
  second. Scoping by path — "the rule governs `relay/*_io.py`" — would be
  circular, defining the rule by where the compliant code already sits. Scoping
  by semantics predicts the next case correctly: a future module under `tools/`
  that reads an artifact as input to a decision is in scope wherever it lives.
- **Schema or semantic validation.** These guards answer "is this value
  representable in this format," not "is this spec coherent." Task-spec
  semantic validation belongs to `relay/spec/`.
- **Cross-field and cross-record consistency.** `_check_trigger` rejects
  `duration_ms` on a non-pulse mode because that is a single trigger's internal
  shape. Whether a trace's ticks increase monotonically, or a `recvs` entry
  matches some `sends` entry, is the verifier's business.
- **Format versioning and migration.** Nothing here says a loader must accept
  documents written by an older relay. `trace_io` deliberately rejects the
  pre-`sends`/`recvs` five-key record with a loud `KeyError` rather than
  tolerating it.
- **Test code.** `tests/` may construct malformed documents by any means,
  including hand-written JSON text, in order to prove the guards fire.

## Failure mode this prevents

A contributor adds a serializer for a new artifact. They read the nearest
existing module, see the `_check_*` helpers called from `*_to_dict`, and mirror
it exactly — guards on dump, raw reads on load. Every round-trip test passes,
because a round trip only ever loads bytes the dump side just produced and
already validated. The gap is invisible to the test suite by construction.

Later, something writes that artifact from outside relay — the C++ host, a
regeneration script, a person editing a file by hand — or a file is truncated
mid-write. The corrupt value loads without complaint. A `str` lands where the
verifier expects a `bool` and is truthy for every non-empty string, so
`"false"` reads as a satisfied signal. Or `passed: "no"` coerces to `True` and
a failing verdict reads as green.

Nothing raises. There is no stack trace and no divergence scan to look at,
because at the trace layer everything is consistent — the run really did load
that value. The verdict is confidently wrong, and it is wrong in the direction
that gets believed: green. Recovering trust means auditing every load path in
every serializer, which is exactly the audit stating the rule once makes
unnecessary.

## Examples in this codebase

- **Reference implementation**: `_check_trigger` in
  [relay/generator/trigger_io.py](../../relay/generator/trigger_io.py), called
  from both `trigger_to_dict` and `trigger_from_dict`. Brought to compliance in
  `7a39983` after the #10 review found the load path open.
- **The load-side twin as a test**:
  `test_toggled_mode_no_longer_compiles_to_malformed_st` in
  `tests/test_trigger_io.py` pins the
  `PT := T#Nonems` failure to the load side, where it was actually reachable.
- **Leaf-property enforcement**: `TestVerdictIOPurity` in
  `tests/test_verdict_io.py` parses `relay/verdict_io.py` and fails on any
  `relay.*` import, noting that importing `AssertionResult` to annotate a
  signature "keeps every functional test green while breaking the leaf
  property." That test is what makes rule 3 mechanical rather than advisory.
- **The out-of-scope case**: `tools/expectations.py` writing the expectations
  artifact with a bare `json.dumps`, diffed by `tests/test_expectations.py`.

## Related

- [pipeline_direction_imports.md](pipeline_direction_imports.md) — names
  `relay/trace_io.py` and `relay/verdict_io.py` as leaf modules; rule 3 above
  is what keeps them leaves
- [verification_path_purity.md](verification_path_purity.md) — the closed
  import set the verifier holds; typed reconstruction on a load path would
  breach it
- CLAUDE.md `## Don't` — "Read a field on a load path that the matching dump
  path guards"
