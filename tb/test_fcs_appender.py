"""Cocotb test for pktforge_fcs_appender.

Drives frames of varying lengths into the DUT and checks the appended
FCS byte-exact against `model/fcs.py:compute_fcs` (a zlib.crc32 wrapper).

Scenarios exercised:
 1. Aligned lengths (multiple of 4 bytes): 4, 8, 64, 256. tkeep=0xF on the
    last input beat; the appender emits an extra 4-byte APPEND beat.
 2. Non-aligned lengths (1 to 3 bytes in the last input beat): 1, 2, 3, 5,
    6, 7, 63, 65. Exercises the merge path in S_PASS.
 3. Backpressure: monitor drops ready_prob to 0.4 for a mid-sized frame.
 4. Back-to-back frames: reuse the DUT for multiple frames in one run and
    verify each independently (crc_q reset between frames).
"""

from __future__ import annotations

import os
import random

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR
from .lib.axis import (  # noqa: E402
    AxisMasterBus, AxisMasterDriver,
    AxisSlaveBus, AxisSlaveMonitor,
)

from model.fcs import compute_fcs  # noqa: E402


DATA_W = 32


def _slave_bus_from_dut(dut) -> AxisMasterBus:
    """Bus wired to the DUT's UPSTREAM (slave) side: from driver's POV, master."""
    return AxisMasterBus(
        tdata=dut.s_axis_tdata,
        tkeep=dut.s_axis_tkeep,
        tvalid=dut.s_axis_tvalid,
        tready=dut.s_axis_tready,
        tlast=dut.s_axis_tlast,
    )


def _master_bus_from_dut(dut) -> AxisSlaveBus:
    """Bus wired to the DUT's DOWNSTREAM (master) side: from monitor's POV, slave."""
    return AxisSlaveBus(
        tdata=dut.m_axis_tdata,
        tkeep=dut.m_axis_tkeep,
        tvalid=dut.m_axis_tvalid,
        tready=dut.m_axis_tready,
        tlast=dut.m_axis_tlast,
    )


async def _reset(dut, cycles: int = 5) -> None:
    dut.rst.value = 1
    dut.enable_i.value = 1
    dut.s_axis_tdata.value  = 0
    dut.s_axis_tkeep.value  = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value  = 0
    dut.m_axis_tready.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def test_fcs_appender_build_and_run():
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[str(RTL_DIR / "pktforge_fcs_appender.sv")],
        hdl_toplevel="pktforge_fcs_appender",
        build_dir=str(BUILD_DIR / "fcs_appender"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_fcs_appender",
        test_module="tb.test_fcs_appender",
        testcase="cocotb_fcs_appender_full",
    )


def _make_frame(seed: int, size: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.getrandbits(8) for _ in range(size))


async def _run_frame(driver: AxisMasterDriver, monitor: AxisSlaveMonitor,
                     frame: bytes, *, label: str) -> None:
    await driver.send_frame(frame)
    got = await monitor.recv_frame(timeout_cycles=50_000)
    expected = frame + compute_fcs(frame)
    assert got == expected, (
        f"[{label}] length in={len(frame)} out={len(got)} exp={len(expected)}\n"
        f"  expected FCS = {compute_fcs(frame).hex()}\n"
        f"  got tail     = {got[-4:].hex() if len(got) >= 4 else got.hex()}"
    )


@cocotb.test()
async def cocotb_fcs_appender_full(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await _reset(dut)

    driver = AxisMasterDriver(dut, _slave_bus_from_dut(dut), dut.clk, DATA_W)
    monitor = AxisSlaveMonitor(dut, _master_bus_from_dut(dut), dut.clk, DATA_W, ready_prob=1.0)
    monitor.start()

    # --- 1. Aligned lengths (multiple of 4) ---
    for i, size in enumerate((4, 8, 64, 256)):
        await _run_frame(driver, monitor, _make_frame(0xA0 + i, size),
                         label=f"aligned_{size}")

    # --- 2. Non-aligned lengths ---
    for i, size in enumerate((1, 2, 3, 5, 6, 7, 63, 65)):
        await _run_frame(driver, monitor, _make_frame(0xB0 + i, size),
                         label=f"nonaligned_{size}")

    # --- 3. Backpressure on the downstream ---
    monitor.ready_prob = 0.4
    monitor.seed(0xC0FFEE)
    await _run_frame(driver, monitor, _make_frame(0xC0, 200), label="backpressure")
    monitor.ready_prob = 1.0

    # --- 4. Back-to-back frames (crc_q must reset between frames) ---
    for i, size in enumerate((16, 17, 32, 33, 128)):
        await _run_frame(driver, monitor, _make_frame(0xD0 + i, size),
                         label=f"back2back_{size}")

    # --- 5. Randomized ---
    rng = random.Random(20260830)
    for k in range(8):
        size = rng.randint(1, 400)
        seed = rng.getrandbits(32)
        await _run_frame(driver, monitor, _make_frame(seed, size),
                         label=f"rand{k}_len{size}")
