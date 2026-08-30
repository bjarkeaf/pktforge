"""Cocotb test for pktforge_hdr_builder (with integrated sweep engine).

Byte-exact check of the RTL header builder against `model/golden.py` on the
RoCEv2 path with append_icrc=0 and append_fcs=0.

Scenarios exercised:
 1. Baseline: defaults, size=64, 4 packets, no sweep active.
 2. Larger fixed size (256) that spans many payload lanes.
 3. Non-4-byte-aligned size (127) to exercise partial tkeep on the last beat.
 4. DSCP/ECN/TTL exercised (checksum sensitivity).
 5. ack_req=1, non-default opcode / pkey / dest_qp.
 6. Randomized configs across 4 seeds.
 7. Backpressure: monitor at 40% ready.
 8. src port sweep (min=32768 max=32771 step=1, wraps).
 9. src IP sweep (integer counter across 5 IPs).
10. size sweep (64 -> 96 -> 128 -> wrap).
11. Multi-dim sweep (dst IP and dst port together).
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
from .lib.axis import AxisSlaveBus, AxisSlaveMonitor  # noqa: E402

from model.config import from_dict  # noqa: E402
from model.golden import frame_bytes  # noqa: E402


DATA_W = 32


def _mac_str(mac_int: int) -> str:
    return ":".join(f"{(mac_int >> (8 * (5 - i))) & 0xFF:02x}" for i in range(6))


def _ip_str(ip_int: int) -> str:
    return ".".join(str((ip_int >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _sweep_dict(min_: int, max_: int, step: int) -> dict | None:
    """Return a golden-model sweep dim, or None if this dim is disabled."""
    return None if step == 0 else {"min": min_, "max": max_, "step": step}


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
    sweep_sip=(0, 0, 0),
    sweep_dip=(0, 0, 0),
    sweep_sport=(0, 0, 0),
    sweep_dport=(0, 0, 0),
    sweep_size=None,          # if given, overrides fixed-size behavior
):
    """Assemble a StreamConfig that matches the DUT's driven inputs."""
    if sweep_size is None:
        # Fixed size: use step=1 min=max to keep _sweep_value happy but not vary.
        size_dim = {"min": size, "max": size, "step": 1}
    else:
        size_dim = {"min": sweep_size[0], "max": sweep_size[1], "step": sweep_size[2]}

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
            "src_ip":   _sweep_dict(*sweep_sip),
            "dst_ip":   _sweep_dict(*sweep_dip),
            "src_port": _sweep_dict(*sweep_sport),
            "dst_port": _sweep_dict(*sweep_dport),
            "size":     size_dim,
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
    ip_misc = (
        (cfg_kwargs["ip_ttl"] & 0xFF)
        | ((cfg_kwargs["ip_dscp"] & 0x3F) << 8)
        | ((cfg_kwargs["ip_ecn"] & 0x03) << 14)
    )
    dut.ip_misc_i.value        = ip_misc
    dut.transport_ports_i.value = (src_port & 0xFFFF) | ((dst_port & 0xFFFF) << 16)
    dut.roce_op_pkey_i.value    = (cfg_kwargs["opcode"] & 0xFF) | ((cfg_kwargs["pkey"] & 0xFFFF) << 8)
    dut.roce_dest_qp_i.value    = cfg_kwargs["dest_qp"] & 0xFFFFFF
    dut.roce_psn_ack_i.value    = (cfg_kwargs["psn_start"] & 0xFFFFFF) | (int(cfg_kwargs["ack_req"]) << 24)

    # Sweep params: use zeros for disabled dims (step=0 → pass base through).
    sip_min, sip_max, sip_step = cfg_kwargs.get("sweep_sip", (0, 0, 0))
    dip_min, dip_max, dip_step = cfg_kwargs.get("sweep_dip", (0, 0, 0))
    sport_min, sport_max, sport_step = cfg_kwargs.get("sweep_sport", (0, 0, 0))
    dport_min, dport_max, dport_step = cfg_kwargs.get("sweep_dport", (0, 0, 0))
    if cfg_kwargs.get("sweep_size") is not None:
        size_min, size_max, size_step = cfg_kwargs["sweep_size"]
    else:
        # Fixed size: min=max=size, step=0 (sweep passes base through).
        size_min = cfg_kwargs["size"]
        size_max = cfg_kwargs["size"]
        size_step = 0

    dut.sweep_sip_min_i.value    = sip_min & 0xFFFFFFFF
    dut.sweep_sip_max_i.value    = sip_max & 0xFFFFFFFF
    dut.sweep_sip_step_i.value   = sip_step & 0xFFFFFFFF
    dut.sweep_dip_min_i.value    = dip_min & 0xFFFFFFFF
    dut.sweep_dip_max_i.value    = dip_max & 0xFFFFFFFF
    dut.sweep_dip_step_i.value   = dip_step & 0xFFFFFFFF
    dut.sweep_sport_min_i.value  = sport_min & 0xFFFF
    dut.sweep_sport_max_i.value  = sport_max & 0xFFFF
    dut.sweep_sport_step_i.value = sport_step & 0xFFFF
    dut.sweep_dport_min_i.value  = dport_min & 0xFFFF
    dut.sweep_dport_max_i.value  = dport_max & 0xFFFF
    dut.sweep_dport_step_i.value = dport_step & 0xFFFF
    dut.sweep_size_min_i.value   = size_min & 0xFFF
    dut.sweep_size_max_i.value   = size_max & 0xFFF
    dut.sweep_size_step_i.value  = size_step & 0xFFF


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
    for sig in ("sweep_sip_min_i", "sweep_sip_max_i", "sweep_sip_step_i",
                "sweep_dip_min_i", "sweep_dip_max_i", "sweep_dip_step_i",
                "sweep_sport_min_i", "sweep_sport_max_i", "sweep_sport_step_i",
                "sweep_dport_min_i", "sweep_dport_max_i", "sweep_dport_step_i",
                "sweep_size_min_i", "sweep_size_max_i", "sweep_size_step_i"):
        getattr(dut, sig).value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


async def _pulse_start(dut) -> None:
    # Sync to an edge first so the value write lands in the ReadWrite phase
    # before the next sampling edge (cocotb+Verilator write-timing gotcha).
    await RisingEdge(dut.clk)
    dut.start_i.value = 1
    await RisingEdge(dut.clk)
    dut.start_i.value = 0
    await RisingEdge(dut.clk)


async def _trigger_packet(dut, *, ready_timeout: int = 1000) -> None:
    await RisingEdge(dut.clk)
    for _ in range(ready_timeout):
        if int(dut.pkt_ready_o.value) == 1:
            break
        await RisingEdge(dut.clk)
    else:
        raise RuntimeError(
            f"pkt_ready_o never went high in {ready_timeout} cycles"
        )
    dut.pkt_valid_i.value = 1
    await RisingEdge(dut.clk)
    dut.pkt_valid_i.value = 0


async def _run_scenario(dut, monitor: AxisSlaveMonitor, cfg_kwargs, *, label: str) -> None:
    _drive_config(dut, cfg_kwargs)
    await RisingEdge(dut.clk)
    await _pulse_start(dut)

    for _ in range(cfg_kwargs["packet_count"]):
        await _trigger_packet(dut)

    frames_got = await monitor.recv_n_frames(
        cfg_kwargs["packet_count"], timeout_cycles=20_000
    )
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
        sources=[
            str(RTL_DIR / "pktforge_sweep.sv"),
            str(RTL_DIR / "pktforge_hdr_builder.sv"),
        ],
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

    # --- 1. Baseline: defaults, size=64, 4 packets, no sweep ---
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

    # Restore full ready for sweep scenarios.
    monitor.ready_prob = 1.0

    # --- 8. src port sweep (32768..32771 step=1) ---
    await _reset(dut)
    scen8 = dict(base)
    scen8.update(size=64, packet_count=6, sweep_sport=(32768, 32771, 1))
    await _run_scenario(dut, monitor, scen8, label="sweep_sport")

    # --- 9. src IP sweep (5 IPs) ---
    await _reset(dut)
    scen9 = dict(base)
    scen9.update(size=64, packet_count=8, sweep_sip=(0x0A000001, 0x0A000005, 1))
    await _run_scenario(dut, monitor, scen9, label="sweep_sip")

    # --- 10. size sweep (64, 96, 128, wrap) ---
    await _reset(dut)
    scen10 = dict(base)
    scen10.update(size=64, packet_count=6, sweep_size=(64, 128, 32))
    await _run_scenario(dut, monitor, scen10, label="sweep_size")

    # --- 11. Multi-dim: dst IP and dst port sweep together ---
    await _reset(dut)
    scen11 = dict(base)
    scen11.update(
        size=80, packet_count=5,
        sweep_dip=(0x0A000010, 0x0A000013, 1),
        sweep_dport=(5000, 5003, 1),
    )
    await _run_scenario(dut, monitor, scen11, label="sweep_multi")
