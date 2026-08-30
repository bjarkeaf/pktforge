"""Cocotb test for pktforge_icrc_appender.

Feeds RoCEv2 frames (Eth + IPv4 + UDP + BTH + payload) into the DUT and
checks the appended ICRC byte-exact against `model/icrc.py:compute_icrc_ipv4`.

Frames come from `model/golden.py:frame_bytes` with append_icrc=False and
append_fcs=False, so the input is a bare pre-ICRC RoCEv2 frame. Expected
output is `frame + compute_icrc_ipv4(ip_hdr, udp_hdr, bth_hdr, b'', payload)`.

Scenarios exercised:
 1. Fixed baseline size=64, several PSNs.
 2. Non-aligned sizes (63, 65, 67) to exercise ICRC-merge into partial beats.
 3. Larger frames (128, 256) that span many beats.
 4. Backpressure on the downstream.
 5. Randomized configs.
"""

from __future__ import annotations

import os
import random

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")
pytest.importorskip("scapy", reason="scapy not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR
from .lib.axis import (  # noqa: E402
    AxisMasterBus, AxisMasterDriver,
    AxisSlaveBus, AxisSlaveMonitor,
)

from model.config import from_dict  # noqa: E402
from model.golden import frame_bytes  # noqa: E402
from model.icrc import compute_icrc_ipv4  # noqa: E402


DATA_W = 32


def _slave_bus_from_dut(dut) -> AxisMasterBus:
    return AxisMasterBus(
        tdata=dut.s_axis_tdata,
        tkeep=dut.s_axis_tkeep,
        tvalid=dut.s_axis_tvalid,
        tready=dut.s_axis_tready,
        tlast=dut.s_axis_tlast,
    )


def _master_bus_from_dut(dut) -> AxisSlaveBus:
    return AxisSlaveBus(
        tdata=dut.m_axis_tdata,
        tkeep=dut.m_axis_tkeep,
        tvalid=dut.m_axis_tvalid,
        tready=dut.m_axis_tready,
        tlast=dut.m_axis_tlast,
    )


async def _reset(dut, cycles: int = 5) -> None:
    dut.rst.value = 1
    dut.s_axis_tdata.value  = 0
    dut.s_axis_tkeep.value  = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value  = 0
    dut.m_axis_tready.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def _make_roce_cfg(*, size: int, psn_start: int, packet_count: int = 1, **overrides):
    cfg = {
        "frame_type": "roce_v2",
        "eth": {"src_mac": "02:00:00:00:00:01", "dst_mac": "02:00:00:00:00:02", "vlan": None},
        "ip": {"src": "10.0.0.1", "dst": "10.0.0.2", "ttl": 64, "dscp": 0, "ecn": 0},
        "transport": {"src_port": 32768, "dst_port": 4791},
        "roce": {"opcode": 0x64, "pkey": 0xFFFF, "dest_qp": 0x10,
                 "ack_req": False, "psn_start": psn_start},
        "http": None,
        "sweep": {"src_ip": None, "dst_ip": None, "src_port": None, "dst_port": None,
                  "size": {"min": size, "max": size, "step": 1}},
        "rate": {"mode": "line_percent", "line_percent": 100, "ifg_bytes": None},
        "output": {"append_fcs": False, "append_icrc": False},
        "run": {"packet_count": packet_count, "seed": 0},
    }
    if overrides:
        for k, v in overrides.items():
            # shallow-merge for the common top-level fields
            if isinstance(v, dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return from_dict(cfg)


def _expected_icrc(frame: bytes) -> bytes:
    """Extract RoCEv2 headers from a pre-ICRC frame and compute ICRC."""
    assert len(frame) >= 54, f"frame too short: {len(frame)}"
    ip_hdr  = frame[14:34]
    udp_hdr = frame[34:42]
    bth_hdr = frame[42:54]
    payload = frame[54:]
    return compute_icrc_ipv4(ip_hdr, udp_hdr, bth_hdr, b"", payload)


async def _run_frame(driver: AxisMasterDriver, monitor: AxisSlaveMonitor,
                     frame: bytes, *, label: str) -> None:
    await driver.send_frame(frame)
    got = await monitor.recv_frame(timeout_cycles=50_000)
    expected = frame + _expected_icrc(frame)
    assert got == expected, (
        f"[{label}] in={len(frame)} out={len(got)} exp={len(expected)}\n"
        f"  expected ICRC tail = {_expected_icrc(frame).hex()}\n"
        f"  got tail           = {got[-4:].hex() if len(got) >= 4 else got.hex()}"
    )


def test_icrc_appender_build_and_run():
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[str(RTL_DIR / "pktforge_icrc_appender.sv")],
        hdl_toplevel="pktforge_icrc_appender",
        build_dir=str(BUILD_DIR / "icrc_appender"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_icrc_appender",
        test_module="tb.test_icrc_appender",
        testcase="cocotb_icrc_appender_full",
    )


@cocotb.test()
async def cocotb_icrc_appender_full(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await _reset(dut)

    driver = AxisMasterDriver(dut, _slave_bus_from_dut(dut), dut.clk, DATA_W)
    monitor = AxisSlaveMonitor(dut, _master_bus_from_dut(dut), dut.clk, DATA_W, ready_prob=1.0)
    monitor.start()

    # --- 1. Baseline size=64, several PSNs ---
    for i, psn in enumerate((0, 1, 42, 0xFFFFFF)):
        cfg = _make_roce_cfg(size=64, psn_start=psn)
        frame = frame_bytes(cfg, 0)
        await _run_frame(driver, monitor, frame, label=f"psn_{psn:x}")

    # --- 2. Non-aligned sizes (last input beat has partial tkeep) ---
    for size in (63, 65, 67):
        cfg = _make_roce_cfg(size=size, psn_start=0x1234)
        frame = frame_bytes(cfg, 0)
        await _run_frame(driver, monitor, frame, label=f"size{size}")

    # --- 3. Larger frames ---
    for size in (128, 256):
        cfg = _make_roce_cfg(size=size, psn_start=0)
        frame = frame_bytes(cfg, 0)
        await _run_frame(driver, monitor, frame, label=f"size{size}")

    # --- 4. Backpressure ---
    monitor.ready_prob = 0.4
    monitor.seed(0xB16B00B5)
    cfg = _make_roce_cfg(size=200, psn_start=99)
    frame = frame_bytes(cfg, 0)
    await _run_frame(driver, monitor, frame, label="backpressure")
    monitor.ready_prob = 1.0

    # --- 5. Randomized configs ---
    rng = random.Random(20260830)
    for k in range(6):
        size = rng.choice([54, 64, 72, 96, 128, 200, 256])
        psn = rng.getrandbits(24)
        ttl = rng.randint(1, 255)
        dscp = rng.getrandbits(6)
        ecn = rng.getrandbits(2)
        cfg = _make_roce_cfg(
            size=size, psn_start=psn,
            ip={"src": "10.1.2.3", "dst": "192.168.5.6",
                "ttl": ttl, "dscp": dscp, "ecn": ecn},
            roce={"opcode": rng.getrandbits(8), "pkey": rng.getrandbits(16),
                  "dest_qp": rng.getrandbits(24), "ack_req": bool(rng.getrandbits(1)),
                  "psn_start": psn},
        )
        frame = frame_bytes(cfg, 0)
        await _run_frame(driver, monitor, frame, label=f"rand{k}_size{size}")
