"""Cocotb test for pktforge_hdr_builder.

Byte-exact check of the RTL header builder against `model/golden.py` on the
RoCEv2 path with append_icrc=0 and append_fcs=0.

Scenarios exercised:
 1. Default config, 4 packets, size=64. PSN advances by 1 per packet.
 2. Larger fixed size (256) that spans many payload lanes.
 3. Non-4-byte-aligned size (127) to exercise partial tkeep on the last beat.
 4. Randomized MAC / IP / port / opcode / PSN with a fresh seed.
 5. Same as (1) but with 50% ready backpressure on the monitor.

Each scenario asserts each emitted frame is byte-identical to
`golden.frame_bytes(cfg, i)`.
"""

from __future__ import annotations

import os
import random

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")
pytest.importorskip("scapy", reason="scapy not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge, Timer  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR
from .lib.axis import AxisSlaveBus, AxisSlaveMonitor  # noqa: E402

from model.config import from_dict  # noqa: E402
from model.golden import frame_bytes  # noqa: E402


DATA_W = 32


def _mac_str(mac_int: int) -> str:
    return ":".join(f"{(mac_int >> (8 * (5 - i))) & 0xFF:02x}" for i in range(6))


def _ip_str(ip_int: int) -> str:
    return ".".join(str((ip_int >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _make_cfg(
    *,
    eth_src_mac: int,
    eth_dst_mac: int,
    ip_src: int,
    ip_dst: int,
    ip_ttl: int,
    ip_dscp: int,
    ip_ecn: int,
    src_port: int,
    dst_port: int,
    opcode: int,
    pkey: int,
    dest_qp: int,
    ack_req: bool,
    psn_start: int,
    size: int,
    packet_count: int,
):
    """Assemble a StreamConfig that matches the DUT's driven inputs."""
    return from_dict({
        "frame_type": "roce_v2",
        "eth": {
            "src_mac": _mac_str(eth_src_mac),
            "dst_mac": _mac_str(eth_dst_mac),
            "vlan": None,
        },
        "ip": {
            "src": _ip_str(ip_src),
            "dst": _ip_str(ip_dst),
            "ttl": ip_ttl,
            "dscp": ip_dscp,
            "ecn": ip_ecn,
        },
        "transport": {"src_port": src_port, "dst_port": dst_port},
        "roce": {
            "opcode": opcode,
            "pkey": pkey,
            "dest_qp": dest_qp,
            "ack_req": ack_req,
            "psn_start": psn_start,
        },
        "http": None,
        "sweep": {
            "src_ip": None, "dst_ip": None,
            "src_port": None, "dst_port": None,
            # step=1 with min==max keeps the size fixed and avoids
            # _sweep_value's divide-by-zero when step is 0.
            "size": {"min": size, "max": size, "step": 1},
        },
        "rate": {"mode": "line_percent", "line_percent": 100, "ifg_bytes": None},
        "output": {"append_fcs": False, "append_icrc": False},
        "run": {"packet_count": packet_count, "seed": 0},
    })


def _drive_config(dut, cfg_kwargs) -> None:
    """Latch a config onto the DUT input pins. Values match regfile encoding."""
    src_port = cfg_kwargs["src_port"]
    dst_port = cfg_kwargs["dst_port"]
    dut.eth_src_mac_i.value    = cfg_kwargs["eth_src_mac"]
    dut.eth_dst_mac_i.value    = cfg_kwargs["eth_dst_mac"]
    dut.ip_src_i.value         = cfg_kwargs["ip_src"]
    dut.ip_dst_i.value         = cfg_kwargs["ip_dst"]
    # IP_MISC: TTL[7:0], DSCP[13:8], ECN[15:14]
    ip_misc = (
        (cfg_kwargs["ip_ttl"] & 0xFF)
        | ((cfg_kwargs["ip_dscp"] & 0x3F) << 8)
        | ((cfg_kwargs["ip_ecn"] & 0x03) << 14)
    )
    dut.ip_misc_i.value        = ip_misc
    # TRANSPORT_PORTS: sport[15:0], dport[31:16]
    dut.transport_ports_i.value = (src_port & 0xFFFF) | ((dst_port & 0xFFFF) << 16)
    # ROCE_OP_PKEY: opcode[7:0], pkey[23:8]
    dut.roce_op_pkey_i.value    = (cfg_kwargs["opcode"] & 0xFF) | ((cfg_kwargs["pkey"] & 0xFFFF) << 8)
    dut.roce_dest_qp_i.value    = cfg_kwargs["dest_qp"] & 0xFFFFFF
    # ROCE_PSN_ACK: psn_start[23:0], ack_req[24]
    dut.roce_psn_ack_i.value    = (cfg_kwargs["psn_start"] & 0xFFFFFF) | (int(cfg_kwargs["ack_req"]) << 24)
    dut.sweep_size_min_i.value  = cfg_kwargs["size"] & 0xFFF


def _make_axis_bus(dut) -> AxisSlaveBus:
    return AxisSlaveBus(
        tdata=dut.m_axis_tdata,
        tkeep=dut.m_axis_tkeep,
        tvalid=dut.m_axis_tvalid,
        tready=dut.m_axis_tready,
        tlast=dut.m_axis_tlast,
    )


async def _reset(dut, cycles: int = 5) -> None:
    dut.rst.value = 1
    dut.start_i.value = 0
    dut.pkt_valid_i.value = 0
    dut.eth_src_mac_i.value = 0
    dut.eth_dst_mac_i.value = 0
    dut.ip_src_i.value = 0
    dut.ip_dst_i.value = 0
    dut.ip_misc_i.value = 0
    dut.transport_ports_i.value = 0
    dut.roce_op_pkey_i.value = 0
    dut.roce_dest_qp_i.value = 0
    dut.roce_psn_ack_i.value = 0
    dut.sweep_size_min_i.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def _pulse_start(dut) -> None:
    await RisingEdge(dut.clk)
    dut.start_i.value = 1
    await RisingEdge(dut.clk)
    dut.start_i.value = 0
    await RisingEdge(dut.clk)


async def _trigger_packet(dut) -> None:
    # Sync to an edge, then wait for ready, then pulse valid.
    await RisingEdge(dut.clk)
    for _ in range(1000):
        if int(dut.pkt_ready_o.value) == 1:
            break
        await RisingEdge(dut.clk)
    else:
        raise RuntimeError(
            f"pkt_ready_o never went high after 1000 cycles; "
            f"pkt_ready_o={dut.pkt_ready_o.value} tvalid={dut.m_axis_tvalid.value}"
        )
    dut.pkt_valid_i.value = 1
    await RisingEdge(dut.clk)
    dut.pkt_valid_i.value = 0
    dut._log.info(
        f"trigger fired: pkt_ready_o={dut.pkt_ready_o.value} "
        f"tvalid={dut.m_axis_tvalid.value} tready={dut.m_axis_tready.value}"
    )


async def _run_scenario(dut, monitor: AxisSlaveMonitor, cfg_kwargs, *, label: str) -> None:
    dut._log.info(f"[{label}] scenario begin: size={cfg_kwargs['size']} psn={cfg_kwargs['psn_start']:#x} n={cfg_kwargs['packet_count']}")
    _drive_config(dut, cfg_kwargs)
    await RisingEdge(dut.clk)
    await _pulse_start(dut)
    dut._log.info(f"[{label}] after start pulse: pkt_ready_o={dut.pkt_ready_o.value}")

    for i in range(cfg_kwargs["packet_count"]):
        await _trigger_packet(dut)
        dut._log.info(f"[{label}] triggered packet {i}")

    dut._log.info(f"[{label}] awaiting {cfg_kwargs['packet_count']} frames from monitor")
    frames_got = await monitor.recv_n_frames(cfg_kwargs["packet_count"], timeout_cycles=20_000)
    dut._log.info(f"[{label}] received {len(frames_got)} frames")
    cfg = _make_cfg(**cfg_kwargs)
    for i, got in enumerate(frames_got):
        expected = frame_bytes(cfg, i)
        assert got == expected, (
            f"[{label}] frame {i}: mismatch\n"
            f"  size expected={len(expected)} got={len(got)}\n"
            f"  expected={expected.hex()}\n"
            f"  got     ={got.hex()}"
        )


def test_hdr_builder_build_and_run():
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[str(RTL_DIR / "pktforge_hdr_builder.sv")],
        hdl_toplevel="pktforge_hdr_builder",
        build_dir=str(BUILD_DIR / "hdr_builder"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_hdr_builder",
        test_module="tb.test_hdr_builder",
        testcase="cocotb_hdr_builder_full",
    )


@cocotb.test()
async def cocotb_hdr_builder_full(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await _reset(dut)

    monitor = AxisSlaveMonitor(dut, _make_axis_bus(dut), dut.clk, DATA_W, ready_prob=1.0)
    monitor.start()

    base = dict(
        eth_src_mac=0x020000000001,
        eth_dst_mac=0x020000000002,
        ip_src=0x0A000001,
        ip_dst=0x0A000002,
        ip_ttl=64,
        ip_dscp=0,
        ip_ecn=0,
        src_port=32768,
        dst_port=4791,
        opcode=0x64,
        pkey=0xFFFF,
        dest_qp=0x10,
        ack_req=False,
        psn_start=0,
        size=64,
        packet_count=4,
    )

    # --- 1. Baseline: defaults, size=64, 4 packets ---
    await _run_scenario(dut, monitor, dict(base), label="baseline")

    # --- 2. Larger frame, multi-lane payload ---
    await _reset(dut)
    scen2 = dict(base); scen2["size"] = 256; scen2["psn_start"] = 100; scen2["packet_count"] = 3
    await _run_scenario(dut, monitor, scen2, label="size256")

    # --- 3. Non-4-byte-aligned size (partial tkeep on last beat) ---
    await _reset(dut)
    scen3 = dict(base); scen3["size"] = 127; scen3["psn_start"] = 0xABCDE; scen3["packet_count"] = 2
    await _run_scenario(dut, monitor, scen3, label="size127")

    # --- 4. DSCP/ECN/TTL exercised (checksum sensitivity) ---
    await _reset(dut)
    scen4 = dict(base)
    scen4.update(ip_ttl=17, ip_dscp=0x2E, ip_ecn=0x2, size=96, psn_start=0x0F0F0F, packet_count=2)
    await _run_scenario(dut, monitor, scen4, label="tos_ttl")

    # --- 5. ack_req=1, non-default opcode / pkey / dest_qp ---
    await _reset(dut)
    scen5 = dict(base)
    scen5.update(opcode=0x0A, pkey=0x1234, dest_qp=0xABCDEF, ack_req=True, size=72, packet_count=2)
    await _run_scenario(dut, monitor, scen5, label="ack_req")

    # --- 6. Randomized configs (fresh seeds) ---
    rng = random.Random(20260830)
    for k in range(4):
        await _reset(dut)
        scen = dict(
            eth_src_mac=rng.getrandbits(48),
            eth_dst_mac=rng.getrandbits(48),
            ip_src=rng.getrandbits(32),
            ip_dst=rng.getrandbits(32),
            ip_ttl=rng.randint(1, 255),
            ip_dscp=rng.getrandbits(6),
            ip_ecn=rng.getrandbits(2),
            src_port=rng.randint(1024, 65535),
            dst_port=4791,
            opcode=rng.getrandbits(8),
            pkey=rng.getrandbits(16),
            dest_qp=rng.getrandbits(24),
            ack_req=bool(rng.getrandbits(1)),
            psn_start=rng.getrandbits(24),
            size=rng.choice([54, 64, 72, 128, 200, 512]),
            packet_count=3,
        )
        await _run_scenario(dut, monitor, scen, label=f"random{k}")

    # --- 7. Backpressure: mutate the running monitor's ready prob ---
    await _reset(dut)
    monitor.ready_prob = 0.4
    monitor.seed(0xB16B00B5)
    scen7 = dict(base); scen7["size"] = 160; scen7["packet_count"] = 3
    await _run_scenario(dut, monitor, scen7, label="backpressure")
