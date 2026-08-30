"""Cocotb test for pktforge_rate_limiter.

Verifies the IFG-based gating: after `frame_end_i` fires, the trigger is
held low for `ifg_bytes / LANES` cycles before passing through.
"""

from __future__ import annotations

import os

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR


LANES = 4


async def _reset(dut, cycles: int = 3) -> None:
    dut.rst.value = 1
    dut.ifg_bytes_i.value = 0
    dut.frame_end_i.value = 0
    dut.trigger_in_i.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def _pulse_frame_end(dut) -> None:
    await RisingEdge(dut.clk)
    dut.frame_end_i.value = 1
    await RisingEdge(dut.clk)
    dut.frame_end_i.value = 0


def test_rate_limiter_build_and_run():
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[str(RTL_DIR / "pktforge_rate_limiter.sv")],
        hdl_toplevel="pktforge_rate_limiter",
        build_dir=str(BUILD_DIR / "rate_limiter"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_rate_limiter",
        test_module="tb.test_rate_limiter",
        testcase="cocotb_rate_limiter_full",
    )


@cocotb.test()
async def cocotb_rate_limiter_full(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await _reset(dut)

    # --- 1. IFG=0: trigger passes through unchanged ---
    dut.ifg_bytes_i.value = 0
    dut.trigger_in_i.value = 1
    await RisingEdge(dut.clk)
    assert int(dut.trigger_out_o.value) == 1, "IFG=0: trigger should pass"
    dut.trigger_in_i.value = 0

    # --- 2. IFG=4 (1 cycle countdown), pulse frame_end and check ---
    await _reset(dut)
    dut.ifg_bytes_i.value = 4
    await _pulse_frame_end(dut)
    # At this point countdown was loaded to 1 on the edge, then next edge
    # it decrements to 0. Verify trigger is masked at least once, then passes.
    dut.trigger_in_i.value = 1
    saw_masked = False
    saw_allowed = False
    for _ in range(6):
        await RisingEdge(dut.clk)
        t_out = int(dut.trigger_out_o.value)
        if t_out == 0:
            saw_masked = True
        else:
            saw_allowed = True
            break
    assert saw_masked, "IFG=4: trigger should be masked for at least 1 cycle"
    assert saw_allowed, "IFG=4: trigger should eventually pass"
    dut.trigger_in_i.value = 0

    # --- 3. IFG=16 (4 cycle countdown): count exact number of masked cycles ---
    await _reset(dut)
    dut.ifg_bytes_i.value = 16
    await _pulse_frame_end(dut)
    dut.trigger_in_i.value = 1
    masked_cycles = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
        if int(dut.trigger_out_o.value) == 0:
            masked_cycles += 1
        else:
            break
    assert masked_cycles == 4, f"IFG=16: expected 4 masked cycles, got {masked_cycles}"
    dut.trigger_in_i.value = 0

    # --- 4. Re-trigger frame_end mid-countdown reloads the counter ---
    await _reset(dut)
    dut.ifg_bytes_i.value = 32   # 8 cycles
    await _pulse_frame_end(dut)
    dut.trigger_in_i.value = 1
    # Wait a few cycles, then pulse frame_end again to reload
    for _ in range(3):
        await RisingEdge(dut.clk)
        assert int(dut.trigger_out_o.value) == 0
    await _pulse_frame_end(dut)
    # Countdown reloaded to 8; must see at least 8 more masked cycles
    masked = 0
    for _ in range(15):
        await RisingEdge(dut.clk)
        if int(dut.trigger_out_o.value) == 0:
            masked += 1
        else:
            break
    assert masked >= 8, f"reload: expected >=8 masked cycles, got {masked}"
    dut.trigger_in_i.value = 0
