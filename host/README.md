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

Requires GCC 13+ or clang 17+ (`std::expected`) and CMake ≥ 3.22:

```
cmake -S host -B host/build -DCMAKE_BUILD_TYPE=Release
cmake --build host/build -j
```

The first configure fetches asio and googletest via `FetchContent` (pinned
tags) and needs network access; subsequent builds use the populated
`host/build/_deps` cache.

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
`--trace-capacity` (default 100000; the ring warns on stderr when entries drop),
`--plant-endpoint <host:port>` (force the `remote_socket` plant against a
running plant server, overriding the spec's plant block).

The JSONL format is normatively defined by [relay/trace_io.py](../relay/trace_io.py):
sorted keys, integral doubles keep their trailing `.0`, non-finite floats are
rejected at dump time. Records are dumped sorted by `(tick, plc_id)` so file
order is a deterministic function of trace *content*; in-memory append order
is completion order and is genuinely nondeterministic under free-running scan
cycles. The former byte-match against
[tests/golden/conveyor_trace.jsonl](../tests/golden/conveyor_trace.jsonl) was
retired with the scan barrier (#14): the contract is verdict equality, not
byte equality.

## Plant selection

Plants resolve through a registry keyed by the spec's `Plant.type`
([plant_registry.cpp](src/plant_registry.cpp)), mirroring
[relay/strategies/plant.py](../relay/strategies/plant.py). Each plant parses
and validates its own `Plant.config` block — the spec loader passes the config
through as raw JSON. Two types are registered:

- `conveyor` — `LocalStubPlant`, an in-process port of the Python conveyor
  physics; requires `belt_speed_m_per_s`, `sensor_trigger_threshold_m`,
  `actuator_latency_ms`.
- `remote_socket` — `RemoteSocketPlant`, a JSON-over-TCP client for a plant
  running in a separate process; requires `endpoint`, accepts
  `request_timeout_ms`. The wire protocol is specified in
  [docs/protocol/plant_socket.md](../docs/protocol/plant_socket.md), and
  [tools/plant_server.py](../tools/plant_server.py) serves any registered
  Python plant over it:

```
python -m tools.plant_server specs/conveyor_handoff.yaml --port 0   # prints READY <port>
host/build/relay_host_main --spec ... --st-blocks ... --out ... --plant-endpoint 127.0.0.1:<port>
```

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

`simclock_only_time_source` applies in full, not "in spirit". Scan cycles are
**free-running** (#14): each PLC coroutine paces itself on its own
`asio::steady_timer` at `scan_period_ms` and produces its own `SimClock` —
`tick` increments per scan, `elapsed_ms` accumulates by that PLC's scan
period, never read from wall clock. Timer deadlines are absolute multiples of
the period from loop start, so a scan that overruns runs late and catches up;
drift never accumulates. The plant runs on its own identically paced loop.
The wall clock decides *when* a scan runs, never *what time it believes it
is* — every value that reaches the trace derives from scan counts and the
scan period.

There is no scan barrier and no shared scan index. Record interleaving and
message-arrival scans are genuinely nondeterministic run to run; what the
host guarantees instead is **verdict determinism with quantified headroom**
— the same certified verdicts on every run, gated by ten consecutive passes
in `tests/test_host_satisfies_expectations.py`. The budgets are measured
(#8), and the headroom of each against the worst observed side:

| Assertion | Sim | Host (in-process) | Host (socket) | Budget | Headroom |
|---|---|---|---|---|---|
| `EVENTUALLY(part_at_b)` | 290.0ms | 300.0ms | 300.0ms | 400ms | 100ms = 10 scan periods |
| `PRECEDES(handoff_signal, belt_b_enable)` | 10.0ms | 0.0ms | 0.0ms | 50ms | 40ms = 4 scan periods |

`CAUSES` is timing-free by construction and carries no budget. The margins
are additive scan periods, not multipliers: the variability mechanism is
interleaving skew under free-running clocks, which is bounded in periods and
does not scale with the observation.

The gap difference on `PRECEDES` is by design: the host's clock referent is
the wall clock and its in-process channel models a backplane, so it pays
between zero and roughly one period where the sim always charges one (#16).
For comm latency the sim is therefore the **conservative** oracle. That claim
does not extend to plant transit: the host observes `part_at_b` one scan
period later than the sim (300.0ms against 290.0ms), so the `EVENTUALLY`
budget derives from the host's observation, the worst side. Verdict equality
is per-assertion pass/fail, so the differing gaps do not affect it.

## Asio coupling

Standalone asio (pinned via `FetchContent`) supplies the entire concurrency
vocabulary through `include/relay_host/async.hpp` — `Executor`, `Channel<T>`,
`Task`; no other header includes asio directly. Everything runs on one
`asio::io_context` with `run()` called from exactly one thread —
single-threaded cooperative scheduling, matching Python's asyncio. PLC scan
loops and the plant loop are spawned with
`asio::experimental::use_promise` (eager initiation); the promises are
awaited for join at shutdown. Host scan bodies remain exception-free and
record failures as `ScanError` in the trace entry, surfaced at the scan
boundary. A socket round-trip inside a plant call suspends on `co_await` —
there is no blocking call on the scan thread.

Each PLC closes its own `CommBus` receive channel as its scan loop exits, and
a send addressed to a closed receiver is **dropped and counted** rather than
queued (#22). This models a fieldbus dropping frames to an offline consumer,
and it is what keeps a sender from parking forever on a full channel no one
will ever drain — the channel holds `kCommChannelCapacity` messages, and
before this the 65th send to a departed PLC never woke. Closing a channel
still yields messages already queued on it, so a PLC's final drain is
unaffected. `host_main` reports any drops on stderr, per consumer; a
tail-of-run drop is expected whenever the plant routes a level-triggered
signal to a PLC that has finished its scan budget.

## Interim assumption register

Per the project's standing rule on interim steps: every scaffold in `host/` is
registered here and guarded. New scaffolds add rows; they do not get to be
undocumented. Both original rows were lifted by #14:

| # | Assumption | Lifted by | How |
|---|-----------|-----------|-----|
| 1 | **All PLCs share a harness-driven scan barrier** | #14 | Free-running per-PLC pacing and clock production. The named guard tests were retired deliberately (see #14); `test_plcs_reach_different_ticks` now pins the inverse — it fails if a barrier is reintroduced. |
| 2 | **`pluggable_subsystems` deferred for `plant_adapter`** | #14 | Plant registry keyed by `Plant.type` plus per-strategy config parsing (`LocalStubPlant` owns the conveyor fields, `RemoteSocketPlant` owns `endpoint`); the loader no longer knows any plant's config shape. |
