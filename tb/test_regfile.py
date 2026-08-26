"""Cocotb test for pktforge_regfile_axil.

Covers, in order:
 1. Reset defaults: every register reads its documented reset value.
 2. Read-only registers (ID, VERSION) return the correct constants.
 3. Write/read round-trip for every RW register, with a random pattern.
 4. Writes to RO registers return OKAY and leave state unchanged.
 5. Writes and reads to reserved addresses return SLVERR.
 6. Unaligned addresses return SLVERR.
 7. Partial wstrb writes update only the strobed bytes.
 8. CTRL bits (START/STOP/ONE_SHOT) pulse for one cycle then clear.
 9. STATUS mirrors the live inputs; PACKETS_SENT mirrors packets_sent_i.

Byte-exact against docs/regmap.md.
"""

from __future__ import annotations

import os
import random

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge, ReadOnly  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR
from .lib.axil import AxilBus, AxilMaster  # noqa: E402


# --- Register offsets. Kept in sync with docs/regmap.md. ---
A = {
    "ID":                0x00,
    "VERSION":           0x04,
    "CTRL":              0x08,
    "STATUS":            0x0C,
    "FRAME_TYPE":        0x10,
    "ETH_SRC_MAC_LO":    0x14,
    "ETH_SRC_MAC_HI":    0x18,
    "ETH_DST_MAC_LO":    0x1C,
    "ETH_DST_MAC_HI":    0x20,
    "ETH_VLAN":          0x24,
    "IP_SRC":            0x28,
    "IP_DST":            0x2C,
    "IP_MISC":           0x30,
    "TRANSPORT_PORTS":   0x34,
    "ROCE_OP_PKEY":      0x38,
    "ROCE_DEST_QP":      0x3C,
    "ROCE_PSN_ACK":      0x40,
    "SWEEP_SIP_MIN":     0x50,
    "SWEEP_SIP_MAX":     0x54,
    "SWEEP_SIP_STEP":    0x58,
    "SWEEP_DIP_MIN":     0x60,
    "SWEEP_DIP_MAX":     0x64,
    "SWEEP_DIP_STEP":    0x68,
    "SWEEP_SPORT_MIN":   0x70,
    "SWEEP_SPORT_MAX":   0x74,
    "SWEEP_SPORT_STEP":  0x78,
    "SWEEP_DPORT_MIN":   0x80,
    "SWEEP_DPORT_MAX":   0x84,
    "SWEEP_DPORT_STEP":  0x88,
    "SWEEP_SIZE_MIN":    0x90,
    "SWEEP_SIZE_MAX":    0x94,
    "SWEEP_SIZE_STEP":   0x98,
    "RATE_MODE":         0xA0,
    "RATE_LINE_PERCENT": 0xA4,
    "RATE_IFG_BYTES":    0xA8,
    "OUTPUT_OPTS":       0xAC,
    "PACKET_COUNT":      0xB0,
    "SEED":              0xB4,
    "PACKETS_SENT":      0xB8,
}

RESET = {
    "ID":                0x504B5446,
    "VERSION":           0x00010000,
    "CTRL":              0x00000000,
    "STATUS":            0x00000000,
    "FRAME_TYPE":        0x00000000,
    "ETH_SRC_MAC_LO":    0x00000002,
    "ETH_SRC_MAC_HI":    0x00000000,
    "ETH_DST_MAC_LO":    0x00000002,
    "ETH_DST_MAC_HI":    0x00000000,
    "ETH_VLAN":          0x00000000,
    "IP_SRC":            0x0A000001,
    "IP_DST":            0x0A000002,
    "IP_MISC":           0x00000040,
    "TRANSPORT_PORTS":   0x12B78000,
    "ROCE_OP_PKEY":      0x00FFFF64,
    "ROCE_DEST_QP":      0x00000010,
    "ROCE_PSN_ACK":      0x00000000,
    "SWEEP_SIP_MIN":     0x00000000,
    "SWEEP_SIP_MAX":     0x00000000,
    "SWEEP_SIP_STEP":    0x00000001,
    "SWEEP_DIP_MIN":     0x00000000,
    "SWEEP_DIP_MAX":     0x00000000,
    "SWEEP_DIP_STEP":    0x00000000,
    "SWEEP_SPORT_MIN":   0x00000000,
    "SWEEP_SPORT_MAX":   0x00000000,
    "SWEEP_SPORT_STEP":  0x00000000,
    "SWEEP_DPORT_MIN":   0x00000000,
    "SWEEP_DPORT_MAX":   0x00000000,
    "SWEEP_DPORT_STEP":  0x00000000,
    "SWEEP_SIZE_MIN":    0x00000040,
    "SWEEP_SIZE_MAX":    0x00000040,
    "SWEEP_SIZE_STEP":   0x00000000,
    "RATE_MODE":         0x00000000,
    "RATE_LINE_PERCENT": 0x00000064,
    "RATE_IFG_BYTES":    0x0000000C,
    "OUTPUT_OPTS":       0x00000003,
    "PACKET_COUNT":      0x00000000,
    "SEED":              0x00000000,
    "PACKETS_SENT":      0x00000000,
}

RO_REGS = {"ID", "VERSION", "STATUS", "PACKETS_SENT"}
RW_REGS = [name for name in A if name not in RO_REGS and name != "CTRL"]

RESERVED_ADDRS = [0x44, 0x48, 0x4C, 0x5C, 0x6C, 0x7C, 0x8C, 0x9C, 0xBC, 0xC0, 0xF8, 0xFC]
UNALIGNED_ADDRS = [0x01, 0x02, 0x03, 0x11, 0x29, 0x53]

RESP_OKAY = 0
RESP_SLVERR = 2


def test_regfile_build_and_run():
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[str(RTL_DIR / "pktforge_regfile_axil.sv")],
        hdl_toplevel="pktforge_regfile_axil",
        build_dir=str(BUILD_DIR / "regfile"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_regfile_axil",
        test_module="tb.test_regfile",
        testcase="cocotb_regfile_full",
    )


def _make_bus(dut) -> AxilBus:
    return AxilBus(
        awaddr=dut.s_axil_awaddr,
        awvalid=dut.s_axil_awvalid,
        awready=dut.s_axil_awready,
        wdata=dut.s_axil_wdata,
        wstrb=dut.s_axil_wstrb,
        wvalid=dut.s_axil_wvalid,
        wready=dut.s_axil_wready,
        bresp=dut.s_axil_bresp,
        bvalid=dut.s_axil_bvalid,
        bready=dut.s_axil_bready,
        araddr=dut.s_axil_araddr,
        arvalid=dut.s_axil_arvalid,
        arready=dut.s_axil_arready,
        rdata=dut.s_axil_rdata,
        rresp=dut.s_axil_rresp,
        rvalid=dut.s_axil_rvalid,
        rready=dut.s_axil_rready,
    )


async def _reset(dut, cycles: int = 5) -> None:
    dut.rst.value = 1
    dut.status_running_i.value = 0
    dut.status_done_i.value = 0
    dut.status_error_i.value = 0
    dut.packets_sent_i.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def cocotb_regfile_full(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await _reset(dut)

    master = AxilMaster(_make_bus(dut), dut.clk)

    # --- 1. Reset defaults on every register ---
    for name, addr in A.items():
        rdata, rresp = await master.read(addr)
        assert rresp == RESP_OKAY, f"{name}: unexpected RRESP {rresp}"
        assert rdata == RESET[name], (
            f"{name} reset: expected {RESET[name]:#010x}, got {rdata:#010x}"
        )

    # --- 3. Round-trip every RW register with a fresh pattern ---
    rng = random.Random(0xC0FFEE)
    for name in RW_REGS:
        pattern = rng.getrandbits(32)
        resp = await master.write(A[name], pattern)
        assert resp == RESP_OKAY, f"{name} write BRESP={resp}"
        rdata, rresp = await master.read(A[name])
        assert rresp == RESP_OKAY, f"{name} read RRESP={rresp}"
        assert rdata == pattern, (
            f"{name}: wrote {pattern:#010x}, read {rdata:#010x}"
        )

    # --- 4. Writes to RO registers return OKAY and don't change state ---
    for name in ("ID", "VERSION"):
        original, _ = await master.read(A[name])
        resp = await master.write(A[name], 0xDEADBEEF)
        assert resp == RESP_OKAY, f"{name} write to RO expected OKAY, got {resp}"
        rdata, _ = await master.read(A[name])
        assert rdata == original, (
            f"{name} unchanged: expected {original:#010x}, got {rdata:#010x}"
        )

    # --- 5. Reserved addresses SLVERR on both write and read ---
    for addr in RESERVED_ADDRS:
        resp = await master.write(addr, 0x11223344)
        assert resp == RESP_SLVERR, f"write @ {addr:#04x} expected SLVERR, got {resp}"
        _, rresp = await master.read(addr)
        assert rresp == RESP_SLVERR, f"read @ {addr:#04x} expected SLVERR, got {rresp}"

    # --- 6. Unaligned addresses SLVERR ---
    for addr in UNALIGNED_ADDRS:
        resp = await master.write(addr, 0x0)
        assert resp == RESP_SLVERR, f"unaligned write @ {addr:#04x} expected SLVERR, got {resp}"
        _, rresp = await master.read(addr)
        assert rresp == RESP_SLVERR, f"unaligned read @ {addr:#04x} expected SLVERR, got {rresp}"

    # --- 7. Partial wstrb only updates matching bytes ---
    await master.write(A["IP_SRC"], 0x11223344, strb=0xF)
    # Overwrite only lower two bytes.
    await master.write(A["IP_SRC"], 0xAABBCCDD, strb=0b0011)
    rdata, _ = await master.read(A["IP_SRC"])
    # Upper two bytes preserved from first write, lower two updated.
    assert rdata == 0x1122CCDD, f"partial-wstrb: expected 0x1122CCDD, got {rdata:#010x}"
    # Overwrite only upper byte.
    await master.write(A["IP_SRC"], 0xEE000000, strb=0b1000)
    rdata, _ = await master.read(A["IP_SRC"])
    assert rdata == 0xEE22CCDD, f"partial-wstrb: expected 0xEE22CCDD, got {rdata:#010x}"

    # --- 8. CTRL bits pulse for one cycle then clear ---
    await _reset(dut)
    # Write START. Sample ctrl_start_o at the same cycle it should be 1.
    # The write happens over a few clocks; the pulse fires the cycle after
    # AW+W are accepted. We poll for a single 1-cycle high.
    cocotb.start_soon(_wait_and_check_pulse(dut, "ctrl_start_o", "START", 0b001))
    resp = await master.write(A["CTRL"], 0b001)
    assert resp == RESP_OKAY

    cocotb.start_soon(_wait_and_check_pulse(dut, "ctrl_stop_o", "STOP", 0b010))
    resp = await master.write(A["CTRL"], 0b010)
    assert resp == RESP_OKAY

    cocotb.start_soon(_wait_and_check_pulse(dut, "ctrl_one_shot_o", "ONE_SHOT", 0b100))
    resp = await master.write(A["CTRL"], 0b100)
    assert resp == RESP_OKAY

    # CTRL reads back as 0 always.
    for _ in range(3):
        rdata, _ = await master.read(A["CTRL"])
        assert rdata == 0, f"CTRL should always read 0, got {rdata:#010x}"

    # --- 9. STATUS mirrors live inputs; PACKETS_SENT mirrors input counter ---
    dut.status_running_i.value = 1
    dut.status_done_i.value = 0
    dut.status_error_i.value = 0
    await RisingEdge(dut.clk)
    rdata, _ = await master.read(A["STATUS"])
    assert rdata == 0b001, f"STATUS with running=1: expected 0b001, got {rdata:#010b}"

    dut.status_running_i.value = 0
    dut.status_done_i.value = 1
    dut.status_error_i.value = 1
    await RisingEdge(dut.clk)
    rdata, _ = await master.read(A["STATUS"])
    assert rdata == 0b110, f"STATUS with done|error: expected 0b110, got {rdata:#010b}"

    dut.packets_sent_i.value = 0xCAFEBABE
    await RisingEdge(dut.clk)
    rdata, _ = await master.read(A["PACKETS_SENT"])
    assert rdata == 0xCAFEBABE, f"PACKETS_SENT: expected 0xCAFEBABE, got {rdata:#010x}"


async def _wait_and_check_pulse(dut, signal_name: str, label: str, expected_bit: int) -> None:
    """Watch a ctrl output signal, expect it to go high for exactly one cycle."""
    sig = getattr(dut, signal_name)
    high_cycles = 0
    saw_high = False
    # Watch for up to 30 cycles; the AXI-Lite handshake usually takes <5.
    for _ in range(30):
        await ReadOnly()
        if int(sig.value) == 1:
            high_cycles += 1
            saw_high = True
        elif saw_high:
            # Fell back to 0: pulse complete.
            break
        await RisingEdge(dut.clk)
    assert saw_high, f"{label}: {signal_name} never went high after CTRL={expected_bit:#05b}"
    assert high_cycles == 1, (
        f"{label}: {signal_name} was high for {high_cycles} cycles, expected 1"
    )
