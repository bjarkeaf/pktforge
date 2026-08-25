"""Smoke test for the cocotb harness.

Exercises `pktforge_passthrough` end-to-end: build with the runner, drive
frames into the slave interface, collect them at the master interface,
byte-compare. Deleted when the first real pktforge module lands and its
test replaces this as the CI floor.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge, Timer  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR
from .lib.axis import (  # noqa: E402
    AxisMasterBus,
    AxisMasterDriver,
    AxisSlaveBus,
    AxisSlaveMonitor,
)


DATA_W = 32


def test_passthrough_build_and_run():
    """Pytest entry point: build the sim with the runner, run the cocotb test."""
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[str(RTL_DIR / "pktforge_passthrough.sv")],
        hdl_toplevel="pktforge_passthrough",
        parameters={"DATA_W": DATA_W},
        build_dir=str(BUILD_DIR / "passthrough"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_passthrough",
        test_module="tb.test_passthrough",
        testcase="cocotb_passthrough_smoke",
    )


@cocotb.test()
async def cocotb_passthrough_smoke(dut):
    """Drive 4 frames of varying sizes, expect byte-identical readback."""
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    dut.rst.value = 1
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tkeep.value = 0
    dut.m_axis_tready.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)

    master = AxisMasterDriver(
        dut,
        AxisMasterBus(
            tdata=dut.s_axis_tdata,
            tkeep=dut.s_axis_tkeep,
            tvalid=dut.s_axis_tvalid,
            tready=dut.s_axis_tready,
            tlast=dut.s_axis_tlast,
        ),
        clk=dut.clk,
        data_w=DATA_W,
        backpressure_prob=0.2,
    )
    slave = AxisSlaveMonitor(
        dut,
        AxisSlaveBus(
            tdata=dut.m_axis_tdata,
            tkeep=dut.m_axis_tkeep,
            tvalid=dut.m_axis_tvalid,
            tready=dut.m_axis_tready,
            tlast=dut.m_axis_tlast,
        ),
        clk=dut.clk,
        data_w=DATA_W,
        ready_prob=0.7,
    )
    slave.start()

    frames = [
        bytes(range(4)),           # exactly one aligned beat
        bytes(range(7)),           # tkeep-partial last beat
        bytes(range(64)),          # multi-beat aligned
        bytes(range(93)),          # multi-beat unaligned
    ]
    for fr in frames:
        await master.send_frame(fr)

    received = await slave.recv_n_frames(len(frames))
    assert received == frames, (
        f"passthrough mismatch:\n  sent    = {[fr.hex() for fr in frames]}\n"
        f"  received = {[r.hex() for r in received]}"
    )
    await Timer(50, units="ns")
