"""Minimal AXI-Stream driver and monitor used by pktforge testbenches.

Handles arbitrary DATA_W (byte-multiple), backpressure, tlast, and tkeep for
non-aligned final beats. Byte order on the AXI-Stream bus: lane 0 = LSB of
tdata carries the first byte of the packet (little-endian byte packing,
matches the AXI-Stream convention used by Xilinx and most soft-IP).

Kept simple on purpose: no tid/tuser/tdest, no interleave. Extend if a test
needs it, do not layer abstractions preemptively.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import AsyncIterator, Iterable

import cocotb
from cocotb.handle import HierarchyObject
from cocotb.triggers import RisingEdge


@dataclass
class AxisMasterBus:
    tdata: HierarchyObject
    tkeep: HierarchyObject
    tvalid: HierarchyObject
    tready: HierarchyObject
    tlast: HierarchyObject


@dataclass
class AxisSlaveBus:
    tdata: HierarchyObject
    tkeep: HierarchyObject
    tvalid: HierarchyObject
    tready: HierarchyObject
    tlast: HierarchyObject


def _pack_beat(chunk: bytes, data_w: int) -> tuple[int, int]:
    """Pack up to data_w/8 bytes into a beat: (tdata_int, tkeep_int).

    First byte of `chunk` occupies lane 0 (LSB of tdata).
    """
    lanes = data_w // 8
    assert len(chunk) <= lanes
    tdata = 0
    tkeep = 0
    for i, b in enumerate(chunk):
        tdata |= b << (8 * i)
        tkeep |= 1 << i
    return tdata, tkeep


def _unpack_beat(tdata: int, tkeep: int, data_w: int) -> bytes:
    lanes = data_w // 8
    out = bytearray()
    for i in range(lanes):
        if (tkeep >> i) & 1:
            out.append((tdata >> (8 * i)) & 0xFF)
    return bytes(out)


class AxisMasterDriver:
    """Drives an AXI-Stream master interface on the DUT (feeds frames in)."""

    def __init__(self, dut, bus: AxisMasterBus, clk, data_w: int, backpressure_prob: float = 0.0):
        self.dut = dut
        self.bus = bus
        self.clk = clk
        self.data_w = data_w
        self.backpressure_prob = backpressure_prob
        self._rng = random.Random(0)

    def seed(self, seed: int) -> None:
        self._rng.seed(seed)

    async def send_frame(self, frame: bytes) -> None:
        lanes = self.data_w // 8
        offset = 0
        while offset < len(frame):
            chunk = frame[offset : offset + lanes]
            is_last = (offset + len(chunk)) >= len(frame)
            tdata, tkeep = _pack_beat(chunk, self.data_w)
            self.bus.tdata.value = tdata
            self.bus.tkeep.value = tkeep
            self.bus.tvalid.value = 1
            self.bus.tlast.value = 1 if is_last else 0
            # Stall randomly to exercise backpressure on the far side.
            if self._rng.random() < self.backpressure_prob:
                self.bus.tvalid.value = 0
                await RisingEdge(self.clk)
                self.bus.tvalid.value = 1
            await RisingEdge(self.clk)
            while self.bus.tready.value == 0:
                await RisingEdge(self.clk)
            offset += len(chunk)
        self.bus.tvalid.value = 0
        self.bus.tlast.value = 0


class AxisSlaveMonitor:
    """Consumes an AXI-Stream slave interface (collects frames the DUT emits)."""

    def __init__(self, dut, bus: AxisSlaveBus, clk, data_w: int, ready_prob: float = 1.0):
        self.dut = dut
        self.bus = bus
        self.clk = clk
        self.data_w = data_w
        self.ready_prob = ready_prob
        self._rng = random.Random(0)
        self._queue: list[bytes] = []
        self._current = bytearray()
        self._task = None

    def seed(self, seed: int) -> None:
        self._rng.seed(seed)

    def start(self) -> None:
        self._task = cocotb.start_soon(self._run())

    async def _run(self) -> None:
        while True:
            self.bus.tready.value = 1 if self._rng.random() < self.ready_prob else 0
            await RisingEdge(self.clk)
            if self.bus.tvalid.value == 1 and self.bus.tready.value == 1:
                tdata = int(self.bus.tdata.value)
                tkeep = int(self.bus.tkeep.value)
                self._current.extend(_unpack_beat(tdata, tkeep, self.data_w))
                if self.bus.tlast.value == 1:
                    self._queue.append(bytes(self._current))
                    self._current = bytearray()

    async def recv_frame(self, timeout_cycles: int = 100_000) -> bytes:
        for _ in range(timeout_cycles):
            if self._queue:
                return self._queue.pop(0)
            await RisingEdge(self.clk)
        raise TimeoutError("no frame received within timeout")

    async def recv_n_frames(self, n: int, timeout_cycles: int = 1_000_000) -> list[bytes]:
        out: list[bytes] = []
        for _ in range(n):
            out.append(await self.recv_frame(timeout_cycles=timeout_cycles))
        return out
