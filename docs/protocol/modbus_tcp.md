# Modbus TCP transport

The wire protocol under the `address` comm strategy's register map. The C++
host is the Modbus **client** for both directions: producers write their coils
at scan phase 6, consumers read theirs at phase 2. The coils live in one
external server — [`tools/modbus_server.py`](../../tools/modbus_server.py) —
which holds a data table and nothing else.

The Python simulator has no Modbus path and never will. It stays the semantic
oracle charging one consumer scan of delivery; the host owns transport
fidelity. This mirrors the plant split exactly, where Python owns `ConveyorPlant`
physics and the host owns `RemoteSocketPlant` transport.

## The poll interval is the consumer's scan top

**One poll per consumer scan, issued as the drain phase. Not a third
independent rate.** `scan_phase_isolation` makes phase 2 the only point at
which inter-PLC data may enter a scan, and a poll on its own timer would land
values between phases. `simclock_only_time_source` forbids a fourth pacing
source beating against scan tops at a frequency no clock in the system
produces. And the delivery rule survives unchanged: a message becomes visible
at the consumer's first scan top strictly later than the sending scan,
satisfied by construction when the poll *is* the scan top.

The cost is a round trip inside the scan. That is already the plant's shape —
the call suspends on `co_await`, no blocking call reaches the scan thread — and
the absolute-deadline timer means an overrun runs late and catches up rather
than accumulating drift.

## Subset

Conformance to a defined subset, decided by what the generator can emit rather
than by what Modbus offers. Every emit mode produces a boolean: `steady`
assigns the condition, `latched` assigns `TRUE`, `pulse` assigns a scratch bit.
No construct in the task spec writes a 16-bit word to a comm signal, so word
access is protocol surface no spec can drive.

| Code | Function | Use |
|---|---|---|
| 0x01 | Read Coils | poll a consumed signal |
| 0x05 | Write Single Coil | emit a produced signal |

**Out of scope:** 0x03 and 0x06 (word access — unreachable, above), 0x02 and
0x04 (read discrete inputs / input registers — read-only, and every comm signal
declares a `produced_by`), 0x0F and 0x10 (write multiple), 0x17, diagnostics,
file records, RTU and ASCII framing, and multi-unit routing.

`AddressStrategy.validate_config` accepts `table: coil` alone, with a distinct
message per rejected table: `discrete_input` and `input_register` are read-only
from the master's perspective, `holding_register` is writable but carries a
word. The grammar keeps all four values so the diagnostic can name them.
`holding_register` becomes reachable the day the generator learns to emit a
word, which is a separate change with its own spec.

## Framing

MBAP header, big-endian throughout:

| Offset | Bytes | Field |
|---|---|---|
| 0 | 2 | Transaction id |
| 2 | 2 | Protocol id — must be 0 |
| 4 | 2 | Length — counts the unit id and everything after it |
| 6 | 1 | Unit id |
| 7 | … | PDU: function code, then data |

The read pump reads the 6-byte length prefix, validates it
(`expected_frame_length`), then awaits exactly `length` further bytes before
handing a whole frame to `decode_response`. Length, protocol id, function code,
and — for read-coils — the byte count are all validated before any payload
access; a decoder faces bytes it did not write.

`Response.payload` carries the meaning-bearing data: for 0x01 the coil bitmap
with its byte-count prefix validated and stripped, so bit 0 of the first
payload byte is the polled coil; for 0x05 the 4-byte echo of address and value.

Requests: `{function, address, value}` where `value` is `0xFF00` / `0x0000` on
a write and the quantity on a read. This client only ever reads quantity 1.

`unit_id` is configurable (`--comm-unit-id`, default 1). A response bearing a
different one is a protocol violation.

## Attribution: the map carries identity, the wire carries the value

Modbus has no field for a sender and none for a sequence number. A coil is one
bit. Receipts are reconstructed so `CAUSES` stays answerable:

| Receipt field | Source |
|---|---|
| `sender` | `CommSignal.produced_by` — static and spec-validated. A register's producer is declared, not guessed, which is stronger than a wire field that could lie. |
| `seq` | the transport's own record of the last **acknowledged** write of that signal, kept on the emit side. Data that traversed the channel, recorded by the channel — not read out of the producer's live scan state. |
| `value` | the register read. The only thing Modbus actually carries. |

Nothing in the trace format, the verifier, or `CAUSES` changes. `ReceiptSlot`
keeps its shape.

**The pairing has a stated bias.** In-process receipts are exact because
`CommBuffer.set` stores seq and value from one message; over a wire carrying
only the value, the pairing cannot be atomic. `poll` therefore reads the
acknowledged-write map *before* it reads the register, so a write landing
between the two shows up as the new value stamped with the previous write's
seq. The stamp can run behind the value, never ahead. Behind is the safe
direction: the verifier locates the sender by `sends[cause].count >=
receipt.seq`, and a recorded count at or above the stamp always exists. The
residue is a ±1-scan ambiguity in `attribution.cause_sent_tick` when a producer
emits every scan — an informational field no verdict reads.

**No acknowledged write, no delivery.** A poll that finds no map entry for a
signal issues no read at all and folds nothing, even if the coil already reads
true. Folding a value without its receipt would let the tag promote into the
consumer's image while `recvs` denies it arrived, manufacturing the exact
`CAUSES` failure receipts exist to prevent. The cost is at most one consumer
scan of extra latency on a signal's first delivery.

**The boundary this has.** It works because one process owns both endpoints. A
deployment where PLCs are separate devices could not synthesize `seq` this way
and would need attribution carried in-band or given up. That is
[#17](https://github.com/qprime/relay/issues/17)'s problem; the technique does
not generalize.

**`recvs` is denser than the in-process trace's.** A receipt is recorded on
every poll of a signal with an acknowledged write, not only when the value
changes. No verdict moves: `CAUSES` anchors on the first *truthy* receipt and
`_is_comm_tag` reads `sends`.

## Plant routes never touch the register file

A sensor wired to the input terminals is sampled at scan top, not delivered
over a network, and `comm_bus_only_inter_plc_channel` exempts plant routes from
the network cost. The plant loop's `bus_.send` is identical under both
transports, and `ModbusTcpTransport::poll` drains the bus channel *before* it
reads the consumed coils, returning both as `PolledValue`s. Bus-drained entries
keep the exact `(sender, seq)` their `Message` carried; only register reads
reconstruct them. A transport that read only registers would leave every spec
with a plant deaf.

The two key spaces are disjoint by the address strategy's collision rules, so
per-key fold order is unobservable.

## Errors and timeouts

- An exception response (`function | 0x80` carrying 0x01 Illegal Function,
  0x02 Illegal Data Address, or 0x03 Illegal Data Value) fails the scan. The
  harness converts it to `RunError` (`CommFailed`) and ends the run; the trace
  retains every record up to the failure.
- EOF, connection reset, a malformed frame, a protocol id other than 0, a unit
  id other than the configured one, a function code that does not answer the
  request, a transaction id matching no request in flight, or a write echo that
  does not match what was written: all fatal transport errors. The client fails
  the in-flight call and makes every subsequent call fail fast without touching
  the socket.
- Each request has a client-side timeout (default 1000 ms). A timeout is
  **fatal, not retried**: a retry would either double-write a register or
  reorder the scan pipeline, and a fieldbus that cannot answer inside a scan
  period has already broken the pacing contract. Same rule as the plant socket.
  The timeout is wall-clock, lives in the adapter, and can never flow into a
  trace value.
- Nothing drops. There is no queue and no closed receiver, so a write to the
  data table succeeds whether or not a consumer ever reads it — correct
  fieldbus behavior, a real data table accepts writes addressed to nobody. The
  host's per-consumer dropped-send warning therefore reports only plant-route
  drops in Modbus mode, which is the only bus traffic left.

## Lifecycle

- The register server is started **externally** with its own copy of the task
  spec; it sizes its coil table from the spec's register map.
- Readiness: the server prints `READY <port>` on stdout once it is listening.
  `--port 0` requests an ephemeral port; the printed value is the bound port.
- Every declared coil initializes to false, matching relay's
  absent-signal-is-falsy semantics.
- The host connects during startup (`HostHarness::try_create`). Connection
  failure is a startup error naming the endpoint — the run never begins. The
  binding check runs **before** the socket does, so a `tag` spec given
  `--comm-endpoint` is rejected for carrying no bindings rather than for
  whatever the endpoint turned out to be.
- Server death mid-run surfaces as `RunError` at the scan that observed it. The
  host does not reconnect.
- The server treats client disconnect as end-of-run and exits.
- `--log <path>` writes one JSON object per request — `{unit_id, function,
  address, quantity?, value, exception?}` — flushed per line. This is what lets
  a loopback test assert that the bytes actually moved: a transport stubbed to
  fall through to the in-process bus produces identical verdicts and cannot
  produce this log.

## Host-side selection

```
relay_host_main --spec resolved_spec.json --st-blocks st_blocks.json \
    --out trace.jsonl --comm-endpoint 127.0.0.1:5020 [--comm-unit-id 1]
```

Absent `--comm-endpoint`, the in-process transport is used. The transport is a
**deployment** choice selected by flag, exactly as `--plant-endpoint` overrides
`Plant.type`; the task spec does not and must not declare it. `--comm-unit-id`
requires `--comm-endpoint`.
