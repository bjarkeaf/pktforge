"""Cocotb test for pktforge_rate_limiter.

Covers both modes:
 - RATE_MODE=0 (IFG): masks the trigger for ifg_bytes/LANES cycles after
   each simulated tlast beat.
 - RATE_MODE=1 (LINE_PERCENT): snoops a fake AXI-Stream feed to accumulate
   frame_bytes, then masks the trigger for
   frame_bytes/LANES * (100 - X)/X cycles.
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
    dut.rate_mode_i.value = 0
    dut.ifg_bytes_i.value = 0
    dut.line_percent_i.value = 100
    dut.axis_tvalid_i.value = 0
    dut.axis_tready_i.value = 0
    dut.axis_tkeep_i.value = 0
    dut.axis_tlast_i.value = 0
    dut.trigger_in_i.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def _emit_fake_frame(dut, byte_count: int) -> None:
    """Drive `byte_count` bytes across the snooped AXI-Stream, tlast on last beat."""
    remaining = byte_count
    await RisingEdge(dut.clk)
    while remaining > 0:
        this_beat = min(LANES, remaining)
        remaining -= this_beat
        dut.axis_tvalid_i.value = 1
        dut.axis_tready_i.value = 1
        dut.axis_tkeep_i.value = (1 << this_beat) - 1
        dut.axis_tlast_i.value = 1 if remaining == 0 else 0
        await RisingEdge(dut.clk)
    dut.axis_tvalid_i.value = 0
    dut.axis_tready_i.value = 0
    dut.axis_tkeep_i.value = 0
    dut.axis_tlast_i.value = 0


async def _count_masked_cycles(dut, max_wait: int = 4000) -> int:
    dut.trigger_in_i.value = 1
    masked = 0
    for _ in range(max_wait):
        await RisingEdge(dut.clk)
        if int(dut.trigger_out_o.value) == 0:
            masked += 1
        else:
            break
    dut.trigger_in_i.value = 0
    return masked


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

    # --- IFG mode ------------------------------------------------------

    # IFG=0: trigger passes through immediately.
    dut.rate_mode_i.value = 0
    dut.ifg_bytes_i.value = 0
    dut.trigger_in_i.value = 1
    await RisingEdge(dut.clk)
    assert int(dut.trigger_out_o.value) == 1, "IFG=0: trigger should pass"
    dut.trigger_in_i.value = 0

    # IFG=16: 4 masked cycles after a tlast beat.
    await _reset(dut)
    dut.rate_mode_i.value = 0
    dut.ifg_bytes_i.value = 16
    await _emit_fake_frame(dut, 64)
    masked = await _count_masked_cycles(dut, max_wait=30)
    assert masked == 4, f"IFG=16: expected 4 masked cycles, got {masked}"

    # --- LINE_PERCENT mode --------------------------------------------

    # 100%: no gating.
    await _reset(dut)
    dut.rate_mode_i.value = 1
    dut.line_percent_i.value = 100
    await _emit_fake_frame(dut, 64)
    masked = await _count_masked_cycles(dut, max_wait=10)
    assert masked == 0, f"LP=100: expected 0 masked cycles, got {masked}"

    # 50%: idle = 64/4 * 50/50 = 16 cycles.
    await _reset(dut)
    dut.rate_mode_i.value = 1
    dut.line_percent_i.value = 50
    await _emit_fake_frame(dut, 64)
    masked = await _count_masked_cycles(dut, max_wait=60)
    assert masked == 16, f"LP=50 F=64: expected 16 masked cycles, got {masked}"

    # 25%: idle = 64/4 * 75/25 = 48 cycles.
    await _reset(dut)
    dut.rate_mode_i.value = 1
    dut.line_percent_i.value = 25
    await _emit_fake_frame(dut, 64)
    masked = await _count_masked_cycles(dut, max_wait=100)
    assert masked == 48, f"LP=25 F=64: expected 48 masked cycles, got {masked}"

    # LP=10 with F=128: idle = 128/4 * 90/10 = 288 cycles.
    await _reset(dut)
    dut.rate_mode_i.value = 1
    dut.line_percent_i.value = 10
    await _emit_fake_frame(dut, 128)
    masked = await _count_masked_cycles(dut, max_wait=500)
    assert masked == 288, f"LP=10 F=128: expected 288 masked cycles, got {masked}"

    # Clamp: LP=0 should behave like 100 (no limit).
    await _reset(dut)
    dut.rate_mode_i.value = 1
    dut.line_percent_i.value = 0
    await _emit_fake_frame(dut, 64)
    masked = await _count_masked_cycles(dut, max_wait=10)
    assert masked == 0, f"LP=0 (clamped to 100): expected 0 masked cycles, got {masked}"

    # Non-aligned frame size (say 65 bytes). LP=50 idle = ceil(65/4) * 50/50 rounded.
    # Formula in RTL: (65 * 50) / (4 * 50) = 3250 / 200 = 16 (integer trunc).
    await _reset(dut)
    dut.rate_mode_i.value = 1
    dut.line_percent_i.value = 50
    await _emit_fake_frame(dut, 65)
    masked = await _count_masked_cycles(dut, max_wait=60)
    assert masked == 16, f"LP=50 F=65: expected 16 masked cycles, got {masked}"
