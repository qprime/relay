from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any


CRC15_POLYNOMIAL = 0x4599


@dataclass(frozen=True)
class EncodedCanFrame:
    bits: tuple[int, ...]
    crc: int
    stuffed_bits: int

    @property
    def bit_count(self) -> int:
        return len(self.bits)


def encode_can_frame(can_id: int, value: bool) -> EncodedCanFrame:
    if isinstance(can_id, bool) or not isinstance(can_id, int) or not 0 <= can_id <= 0x7FF:
        raise ValueError("can_id must be an integer in [0, 2047]")
    if not isinstance(value, bool):
        raise TypeError("CAN Boolean frame value must be bool")
    body = [0]
    body.extend((can_id >> shift) & 1 for shift in range(10, -1, -1))
    body.extend((0, 0, 0))
    body.extend((0, 0, 0, 1))
    byte = 1 if value else 0
    body.extend((byte >> shift) & 1 for shift in range(7, -1, -1))
    crc = 0
    for bit in body:
        feedback = ((crc >> 14) & 1) ^ bit
        crc = (crc << 1) & 0x7FFF
        if feedback:
            crc ^= CRC15_POLYNOMIAL
    stuffed_source = body + [(crc >> shift) & 1 for shift in range(14, -1, -1)]
    stuffed: list[int] = []
    run_bit = -1
    run = 0
    stuff_count = 0
    for bit in stuffed_source:
        stuffed.append(bit)
        if bit == run_bit:
            run += 1
        else:
            run_bit, run = bit, 1
        if run == 5:
            stuffed.append(1 - bit)
            stuff_count += 1
            run_bit, run = 1 - bit, 1
    bits = tuple(stuffed + [1, 1, 1] + [1] * 7 + [1] * 3)
    return EncodedCanFrame(bits, crc, stuff_count)


@dataclass(frozen=True)
class PendingCanFrame:
    signal: str
    can_id: int
    value: Any
    sender: str
    seq: int
    ready_ms: Fraction
    order: int
    bit_count: int


@dataclass(frozen=True)
class CompletedCanFrame:
    frame: PendingCanFrame
    arbitration_start_ms: Fraction
    completion_ms: Fraction


class CanScheduler:
    def __init__(self, baud_rate: int) -> None:
        if isinstance(baud_rate, bool) or not isinstance(baud_rate, int) or baud_rate <= 0:
            raise ValueError("baud_rate must be a positive integer")
        self.baud_rate = baud_rate
        self.available_ms = Fraction(0)
        self._pending: list[PendingCanFrame] = []
        self._next_order = 0
        self._active: CompletedCanFrame | None = None

    def enqueue(
        self, signal: str, can_id: int, value: bool, sender: str, seq: int, ready_ms: float
    ) -> PendingCanFrame:
        encoded = encode_can_frame(can_id, value)
        frame = PendingCanFrame(
            signal,
            can_id,
            value,
            sender,
            seq,
            Fraction(str(ready_ms)),
            self._next_order,
            encoded.bit_count,
        )
        self._next_order += 1
        self._pending.append(frame)
        return frame

    def settle(self, through_ms: float) -> tuple[CompletedCanFrame, ...]:
        limit = Fraction(str(through_ms))
        completed: list[CompletedCanFrame] = []
        while self._active is not None or self._pending:
            if self._active is not None:
                if self._active.completion_ms > limit:
                    break
                completed.append(self._active)
                self._active = None
                continue
            earliest = min(frame.ready_ms for frame in self._pending)
            start = max(self.available_ms, earliest)
            contenders = [frame for frame in self._pending if frame.ready_ms <= start]
            winner = min(contenders, key=lambda frame: (frame.can_id, frame.order))
            finish = start + Fraction(winner.bit_count * 1000, self.baud_rate)
            self._pending.remove(winner)
            self.available_ms = finish
            self._active = CompletedCanFrame(winner, start, finish)
        return tuple(completed)
