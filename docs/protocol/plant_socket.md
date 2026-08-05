# Plant socket protocol

JSON-over-TCP request/response protocol between the C++ host (client) and a
plant process (server). One connection per run; the host is the only client.
The protocol carries **plant traffic only** — sensor routing and actuator
reads. It is not an inter-PLC channel; `comm_bus_only_inter_plc_channel` is
untouched by it.

## Framing

Newline-delimited JSON: UTF-8, one JSON object per line, terminated by `\n`
(LF). No length prefix. A line that does not parse as a JSON object is a
protocol violation; the peer that receives one must treat the connection as
failed (see Errors).

## Correlation

Every request carries an `id`: a positive integer unique for the lifetime of
the connection (the host allocates them monotonically). The response echoes
the request's `id`. Responses may arrive in **any order**; the client
correlates by `id`, never by arrival position. Multiple requests may be in
flight at once.

## Requests and responses

Request: `{"id": <int>, "method": <string>, "params": <object>}`

Success: `{"id": <int>, "result": <object>}`
Failure: `{"id": <int>, "error": {"message": <string>}}`

Signal values are JSON booleans or numbers — the same value domain as trace
cells. Three methods, mirroring the `PlantModel` concept:

### `read_actuators`

Params: `latest_outputs` — object mapping each plc_id to its most recently
published output image (object of key → value).

Result: `actuators` — object mapping actuator alias to value, per the plant's
declared actuator list.

### `step`

Params: `dt_ms` — number; `actuators` — the object returned by
`read_actuators` (echoed back so the server holds no per-request state
between the two calls).

Result: `outputs` — the plant output snapshot after advancing physics by
`dt_ms`. For the conveyor plant: `{"sensor_a_exit_triggered": <bool>,
"part_at_b": <bool>}`.

**The host is authoritative for `dt_ms`.** The server advances physics by
exactly the value given and must not read wall clock for physics —
`simclock_only_time_source` extends over the wire.

### `route_to_plcs`

Params: `current` — an `outputs` object; `prior` — the previous `outputs`
object, or `null` on the first scan. The host owns the prior snapshot so the
server's routing stays a pure function of its arguments.

Result: `routed` — array of `{"to_plc": <plc_id>, "as_key": <string>,
"value": <bool|number>}`. Symbolic names, not indices: the host maps `to_plc`
to a PLC index and `as_key` to a signal id against its own tables, and fails
the scan if either is unknown.

## Worked exchange

One conveyor scan at `dt_ms = 10`, second scan of the run:

```
host →  {"id": 4, "method": "read_actuators", "params": {"latest_outputs": {"plc_a": {"handoff_signal": false}, "plc_b": {"belt_b_enable": false}}}}
host ←  {"id": 4, "result": {"actuators": {"belt_b_enable_signal": false}}}
host →  {"id": 5, "method": "step", "params": {"dt_ms": 10.0, "actuators": {"belt_b_enable_signal": false}}}
host ←  {"id": 5, "result": {"outputs": {"sensor_a_exit_triggered": false, "part_at_b": false}}}
host →  {"id": 6, "method": "route_to_plcs", "params": {"current": {"sensor_a_exit_triggered": false, "part_at_b": false}, "prior": {"sensor_a_exit_triggered": false, "part_at_b": false}}}
host ←  {"id": 6, "result": {"routed": []}}
```

A later scan where the part trips the exit sensor:

```
host →  {"id": 88, "method": "route_to_plcs", "params": {"current": {"sensor_a_exit_triggered": true, "part_at_b": false}, "prior": {"sensor_a_exit_triggered": false, "part_at_b": false}}}
host ←  {"id": 88, "result": {"routed": [{"to_plc": "plc_a", "as_key": "sensor_a_exit", "value": true}]}}
```

## Errors and timeouts

- An `error` response maps to a `PlantError` for that call. The harness
  converts any per-scan `PlantError` into a `RunError` (`PlantFailed`) and
  ends the run; the trace retains every record up to the failure.
- EOF, connection reset, or an unparseable line from the server is a fatal
  transport error: the client fails the in-flight calls with `PlantError`
  and makes every subsequent call fail fast without touching the socket.
- Each request has a client-side timeout (`request_timeout_ms` in the plant
  config, default 1000). A timeout is **fatal, not retried**: a retry would
  either double-step physics or reorder the scan pipeline, and a plant that
  cannot answer inside a scan period has already broken the pacing contract.
  The timeout is wall-clock; it lives in the adapter (harness side) and can
  never flow into a trace value.

## Lifecycle

- The plant server is started **externally** (operator, script, or test
  fixture) with its own copy of the task spec; it owns plant construction
  through the Python registry (`Plant.type` → `get_plant`).
- Readiness: the server prints `READY <port>` on stdout once it is
  listening. `--port 0` requests an ephemeral port; the printed value is the
  bound port.
- The host connects during startup (`try_create`). Connection failure is a
  startup error naming the endpoint — the run never begins.
- Server death mid-run surfaces as `RunError` at the scan that observed it
  (see Errors). The host does not reconnect.
- The server treats client disconnect as end-of-run and exits.

## Host-side plant config

```json
{"type": "remote_socket", "config": {"endpoint": "127.0.0.1:9000", "request_timeout_ms": 1000}}
```

`endpoint` is required (`host:port`). `request_timeout_ms` is optional. The
spec's `routes` and `actuators` blocks are server-side concerns for this
plant type: the server routes symbolically and the host resolves names at
delivery time.
