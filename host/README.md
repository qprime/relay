# relay host — C++23 deployment target

The Python simulator ([relay/runtime/](../relay/runtime/) + [relay/verify/](../relay/verify/))
is the **oracle**: it runs a task spec deterministically, certifies which assertions
hold, and emits an expectations artifact under
[specs/expectations/](../specs/expectations/). This host is the **deployment
target**: it runs the same generated ST in a wall-clock-paced environment and is
judged by whether the verifier — re-applied to the host's JSONL trace — reproduces
the Python-certified verdicts. See the relay root [README](../README.md) and
issue [#4](https://github.com/qprime/relay/issues/4) for the full design.

## Build

Requires GCC 13+ or clang 17+ (`std::expected`), CMake ≥ 3.22, and the Hermes
submodule:

```
git submodule update --init --recursive
cmake -S host -B host/build -DCMAKE_BUILD_TYPE=Release
cmake --build host/build -j
```

`-std=c++23` is load-bearing, not a preference. If the build fails with
`'expected' in namespace 'std' does not name a template type`, the standard flag
is missing — fix `CMakeLists.txt`, do not upgrade the compiler.

## Run

The host reads JSON, never YAML. Python emits the inputs at the language boundary:

```
python -m tools.emit_host_inputs specs/conveyor_handoff.yaml --out-dir /tmp/host_inputs
host/build/relay_host_main \
    --spec /tmp/host_inputs/resolved_spec.json \
    --st-blocks /tmp/host_inputs/st_blocks.json \
    --out /tmp/host_inputs/cpp_trace.jsonl
```

Flags: `--max-scans`, `--scan-period-ms` (default to the resolved-spec values),
`--trace-capacity` (default 100000; the ring warns on stderr when entries drop).

For the conveyor spec at the default `scan_period_ms`/`max_scans`, the emitted
trace byte-matches [tests/golden/conveyor_trace.jsonl](../tests/golden/conveyor_trace.jsonl).
The JSONL format is normatively defined by [relay/trace_io.py](../relay/trace_io.py):
sorted keys, integral doubles keep their trailing `.0`, non-finite floats are
rejected at dump time.

## Expectations workflow

```
python -m tools.regenerate_expectations        # sim → specs/expectations/*.expected.json
pytest tests/test_expectations.py              # committed artifact vs fresh run
pytest tests/test_host_satisfies_expectations.py   # verdict equality vs the C++ trace
```

The artifact is generated, never hand-authored. The contract is verdict
**equality** per assertion — a host that passes an assertion Python failed is as
wrong as the reverse. `witness` and `observed_gap_ms` are informational only.

## Time discipline

`simclock_only_time_source` applies in full, not "in spirit". Every value that
reaches the trace derives from the injected `SimClock`, advanced by exactly
`scan_period_ms` per scan. The wall clock is read in exactly one place —
`HostHarness::run` step 1 (`hce::sleep` between scans) — and affects only how
long the host takes in real time, never trace content. The PLC scan coroutine
(`run_plc_scan_loop`) contains no sleep and no wall-clock read.

Within a scan, the harness releases scan executors **sequentially in `plc_ids`
order** (send clock *i*, await done *i*, then *i+1*). This mirrors Python's
single-threaded asyncio wakeup, makes FB-path comm delivery same-scan
producer-before-consumer, and yields trace-append order = `plc_ids` order — the
property the golden byte-match depends on.

## Hermes coupling

Hermes (`hce`) is vendored as a git submodule at `third_party/hermes/`, pinned to
a known-good commit, built as C++20; the host is C++23 and links against it.
Channels return `hce::awt<bool>` (false on closed); `hce::co<T>` is one stack
frame — sub-coroutines cannot be `co_await`ed directly, which is why the comm
drain loop lives in the scan-loop coroutine body. Channel `send` captures a
pointer to its argument until the awaitable completes: arguments must outlive
the `co_await` of the result (see `CommBus::send`). The library is
exception-coupled and cannot build `-fno-exceptions`; host scan bodies remain
exception-free and record failures as `ScanError` in the trace entry, surfaced
at the scan boundary.

## Interim assumption register

Per the project's standing rule on interim steps: every scaffold in `host/` is
registered here and guarded. New scaffolds add rows; they do not get to be
undocumented.

| # | Assumption | Why it is here | What breaks when lifted | Guard | Lifted by |
|---|-----------|----------------|------------------------|-------|-----------|
| 1 | **All PLCs share a harness-driven scan barrier**, so every PLC occupies the same scan index at every tick | Faithful port of `relay/runtime/harness.py`; makes the C++ trace deterministic and the oracle handoff cheap. Real controllers do not rendezvous. | **Per-scan trace record interleaving**, and with it the byte-match against `tests/golden/conveyor_trace.jsonl`. **Not `PRECEDES`** — that assertion resolves both signals off a single plc_b record and is barrier-independent. | `host/tests/test_host_harness.cpp::test_scan_synchrony_assumption_holds` asserts the shared-scan-index property directly and by name; `test_trace_record_order_is_plc_ids_order` pins the observable consequence. | #14. Budgeted `PRECEDES` (#6) has landed and #12 (`CAUSES`) is the surviving cross-PLC contract. |
| 2 | **`pluggable_subsystems` deferred for `plant_adapter`** | One plant implementation; a registry with one entry and no selector field is ceremony, not compliance. (`comm_strategy` **does** comply: registry keyed by the `Comm.strategy` spec field.) | Nothing today. Framework code must not branch on plant name in the interim. | Code review; this row. | The second plant implementation (#14). |
