from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
from typing import TextIO

from relay.spec.schema import load_spec
from relay.strategies.comm import comm_bindings

READ_COILS = 0x01
WRITE_SINGLE_COIL = 0x05
EXCEPTION_MASK = 0x80

ILLEGAL_FUNCTION = 0x01
ILLEGAL_DATA_ADDRESS = 0x02
ILLEGAL_DATA_VALUE = 0x03

COIL_ON = 0xFF00
COIL_OFF = 0x0000

HEADER_PREFIX_BYTES = 6
MAX_READ_QUANTITY = 2000
COIL_TABLE = "coil"


class ModbusException(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"modbus exception {code}")
        self.code = code


class RegisterFile:
    def __init__(self, addresses: set[int]) -> None:
        self._coils = {address: False for address in addresses}

    def read(self, address: int, quantity: int) -> list[bool]:
        if not 1 <= quantity <= MAX_READ_QUANTITY:
            raise ModbusException(ILLEGAL_DATA_VALUE)
        window = range(address, address + quantity)
        if any(coil not in self._coils for coil in window):
            raise ModbusException(ILLEGAL_DATA_ADDRESS)
        return [self._coils[coil] for coil in window]

    def write(self, address: int, value: int) -> bool:
        if address not in self._coils:
            raise ModbusException(ILLEGAL_DATA_ADDRESS)
        if value not in (COIL_ON, COIL_OFF):
            raise ModbusException(ILLEGAL_DATA_VALUE)
        self._coils[address] = value == COIL_ON
        return self._coils[address]


def coil_addresses(spec_path: Path) -> set[int]:
    return {
        binding.address
        for binding in comm_bindings(load_spec(spec_path)).values()
        if binding.table == COIL_TABLE
    }


def _pack_coils(values: list[bool]) -> bytes:
    byte_count = (len(values) + 7) // 8
    packed = bytearray(byte_count)
    for index, value in enumerate(values):
        if value:
            packed[index // 8] |= 1 << (index % 8)
    return bytes([byte_count]) + bytes(packed)


def _frame(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    return (
        transaction_id.to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + (len(pdu) + 1).to_bytes(2, "big")
        + bytes([unit_id])
        + pdu
    )


class ModbusSession:
    def __init__(self, registers: RegisterFile, unit_id: int, log: TextIO | None) -> None:
        self._registers = registers
        self._unit_id = unit_id
        self._log = log

    def _record(self, entry: dict[str, object]) -> None:
        if self._log is None:
            return
        self._log.write(json.dumps(entry, sort_keys=True) + "\n")
        self._log.flush()

    def dispatch(self, unit_id: int, pdu: bytes) -> bytes:
        function = pdu[0]
        entry: dict[str, object] = {"unit_id": unit_id, "function": function}
        try:
            if unit_id != self._unit_id:
                raise ModbusException(ILLEGAL_DATA_ADDRESS)
            if function not in (READ_COILS, WRITE_SINGLE_COIL):
                raise ModbusException(ILLEGAL_FUNCTION)
            if len(pdu) != 5:
                raise ModbusException(ILLEGAL_DATA_VALUE)
            address = int.from_bytes(pdu[1:3], "big")
            argument = int.from_bytes(pdu[3:5], "big")
            entry["address"] = address
            if function == READ_COILS:
                entry["quantity"] = argument
                values = self._registers.read(address, argument)
                entry["value"] = values[0]
                return bytes([READ_COILS]) + _pack_coils(values)
            entry["value"] = argument == COIL_ON
            self._registers.write(address, argument)
            return pdu
        except ModbusException as exc:
            entry["exception"] = exc.code
            return bytes([function | EXCEPTION_MASK, exc.code])
        finally:
            self._record(entry)


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    session: ModbusSession,
) -> None:
    try:
        while True:
            try:
                header = await reader.readexactly(HEADER_PREFIX_BYTES)
            except asyncio.IncompleteReadError:
                return
            transaction_id = int.from_bytes(header[0:2], "big")
            protocol_id = int.from_bytes(header[2:4], "big")
            length = int.from_bytes(header[4:6], "big")
            if protocol_id != 0 or length < 2:
                return
            try:
                body = await reader.readexactly(length)
            except asyncio.IncompleteReadError:
                return
            response = session.dispatch(body[0], body[1:])
            writer.write(_frame(transaction_id, body[0], response))
            await writer.drain()
    finally:
        writer.close()


async def serve(spec_path: Path, host: str, port: int, unit_id: int, log_path: Path | None) -> None:
    addresses = coil_addresses(spec_path)
    log = log_path.open("w") if log_path is not None else None
    run_over = asyncio.Event()

    async def session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await handle_client(
                reader, writer, ModbusSession(RegisterFile(addresses), unit_id, log)
            )
        finally:
            run_over.set()

    server = await asyncio.start_server(session, host, port)
    bound_port = server.sockets[0].getsockname()[1]
    print(f"READY {bound_port}", flush=True)
    try:
        async with server:
            await run_over.wait()
    finally:
        if log is not None:
            log.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve a spec's coil map over Modbus TCP for the C++ host"
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(args.spec, args.host, args.port, args.unit_id, args.log))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
