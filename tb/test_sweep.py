"""Cocotb test for pktforge_sweep.

Byte-exact against a local Python reference that mirrors
`model/golden.py:_sweep_value`. Each scenario:
 - configures base/min/max/step,
 - pulses start_i,
 - reads value_o (packet_index=0),
 - pulses advance_i N times, comparing value_o after each pulse against
   the reference for packet_index=1..N.

Scenarios exercised:
 1. step=0 (disabled): value_o holds base_i regardless of advance pulses.
 2. step=1 basic: counts min, min+1, ..., max, min, ...
 3. step>1 with non-trivial wrap: e.g. min=10 max=17 step=3 → 10,13,16,10,...
 4. Sweep hits max exactly on a step boundary.
 5. Sweep exceeds max at the wrap step (falls back to min).
 6. Base pass-through: with step=0, value_o = base_i even if min/max carry
    unrelated data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR


def _sweep_value(base: int, min_: int, max_: int, step: int, packet_index: int) -> int:
    """Python mirror of pktforge_sweep semantics.

    Matches model/golden.py:_sweep_value with the additional convention
    that step==0 means "sweep disabled, return base".
    """
    if step == 0:
        return base
    span = (max_ - min_) // step + 1
    return min_ + (packet_index % span) * step


@dataclass
class Scenario:
    label: str
    base: int
    min_: int
    max_: int
    step: int
    n_packets: int


SCENARIOS = [
    # step=0: base pass-through, advance pulses ignored
    Scenario("step0_disabled",       base=0xDEADBEEF, min_=0x1000, max_=0x1FFF, step=0,  n_packets=5),
    # step=1 basic
    Scenario("step1_basic",          base=0x0,        min_=32768,  max_=32770,  step=1,  n_packets=8),
    # step>1 non-trivial wrap
    Scenario("step3_wraps",          base=0x0,        min_=10,     max_=17,     step=3,  n_packets=10),
    # sweep hits max exactly
    Scenario("hits_max_exactly",     base=0x0,        min_=0,      max_=8,      step=4,  n_packets=6),
    # sweep would exceed max: wrap
    Scenario("exceeds_max",          base=0x0,        min_=0,      max_=7,      step=4,  n_packets=6),
    # base pass-through even with unrelated min/max
    Scenario("base_ignores_bounds",  base=0xA5A5A5A5, min_=1,      max_=100,    step=0,  n_packets=3),
    # IP-style values (32-bit)
    Scenario("ip_step_one",          base=0x0A000001, min_=0x0A000001, max_=0x0A000005, step=1, n_packets=8),
]


def _drive(dut, s: Scenario) -> None:
    dut.base_i.value = s.base
    dut.min_i.value  = s.min_
    dut.max_i.value  = s.max_
    dut.step_i.value = s.step


async def _reset(dut, cycles: int = 3) -> None:
    dut.rst.value = 1
    dut.start_i.value = 0
    dut.advance_i.value = 0
    dut.base_i.value = 0
    dut.min_i.value = 0
    dut.max_i.value = 0
    dut.step_i.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def _pulse(dut, name: str) -> None:
    await RisingEdge(dut.clk)
    getattr(dut, name).value = 1
    await RisingEdge(dut.clk)
    getattr(dut, name).value = 0


def test_sweep_build_and_run():
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[str(RTL_DIR / "pktforge_sweep.sv")],
        hdl_toplevel="pktforge_sweep",
        build_dir=str(BUILD_DIR / "sweep"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_sweep",
        test_module="tb.test_sweep",
        testcase="cocotb_sweep_full",
    )


@cocotb.test()
async def cocotb_sweep_full(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await _reset(dut)

    for s in SCENARIOS:
        await _reset(dut)
        _drive(dut, s)
        # Let inputs settle, then start.
        await RisingEdge(dut.clk)
        await _pulse(dut, "start_i")

        # After start_i, value_o should be the packet_index=0 value.
        await RisingEdge(dut.clk)
        expected0 = _sweep_value(s.base, s.min_, s.max_, s.step, 0)
        got0 = int(dut.value_o.value)
        assert got0 == expected0, (
            f"[{s.label}] after start: expected {expected0:#x}, got {got0:#x}"
        )

        # For each of n_packets - 1 advances, verify the sequence.
        for k in range(1, s.n_packets):
            await _pulse(dut, "advance_i")
            await RisingEdge(dut.clk)
            expected = _sweep_value(s.base, s.min_, s.max_, s.step, k)
            got = int(dut.value_o.value)
            assert got == expected, (
                f"[{s.label}] packet {k}: expected {expected:#x}, got {got:#x}"
            )
