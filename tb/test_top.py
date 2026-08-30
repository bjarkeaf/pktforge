"""End-to-end cocotb test for pktforge_top.

Programs the regfile over AXI-Lite, pulses CTRL.START, captures the frames
that come out on the AXI-Stream master, and diffs each byte-exact against
`model/golden.py:frame_bytes` with append_icrc=True and append_fcs=True.
Also verifies PACKETS_SENT reads back == packet_count.

Runs a small matrix of scenarios: fixed size, size sweep, sport sweep.
"""

from __future__ import annotations

import os

import pytest

cocotb = pytest.importorskip("cocotb", reason="cocotb not installed; see docs/setup.md")
pytest.importorskip("scapy", reason="scapy not installed; see docs/setup.md")

from cocotb.clock import Clock  # noqa: E402
from cocotb.triggers import RisingEdge  # noqa: E402
from cocotb_tools.runner import get_runner  # noqa: E402

from .conftest import RTL_DIR, BUILD_DIR
from .lib.axil import AxilBus, AxilMaster  # noqa: E402
from .lib.axis import AxisSlaveBus, AxisSlaveMonitor  # noqa: E402

from model.config import from_dict  # noqa: E402
from model.golden import frame_bytes  # noqa: E402


DATA_W = 32

A = {
    "CTRL":              0x08,
    "STATUS":            0x0C,
    "ETH_SRC_MAC_LO":    0x14,
    "ETH_SRC_MAC_HI":    0x18,
    "ETH_DST_MAC_LO":    0x1C,
    "ETH_DST_MAC_HI":    0x20,
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
    "SWEEP_SPORT_MIN":   0x70,
    "SWEEP_SPORT_MAX":   0x74,
    "SWEEP_SPORT_STEP":  0x78,
    "SWEEP_SIZE_MIN":    0x90,
    "SWEEP_SIZE_MAX":    0x94,
    "SWEEP_SIZE_STEP":   0x98,
    "OUTPUT_OPTS":       0xAC,
    "PACKET_COUNT":      0xB0,
    "PACKETS_SENT":      0xB8,
    "RATE_MODE":         0xA0,
    "RATE_LINE_PERCENT": 0xA4,
    "RATE_IFG_BYTES":    0xA8,
}


def _axil_bus(dut) -> AxilBus:
    return AxilBus(
        awaddr=dut.s_axil_awaddr, awvalid=dut.s_axil_awvalid, awready=dut.s_axil_awready,
        wdata=dut.s_axil_wdata,   wstrb=dut.s_axil_wstrb,     wvalid=dut.s_axil_wvalid,   wready=dut.s_axil_wready,
        bresp=dut.s_axil_bresp,   bvalid=dut.s_axil_bvalid,   bready=dut.s_axil_bready,
        araddr=dut.s_axil_araddr, arvalid=dut.s_axil_arvalid, arready=dut.s_axil_arready,
        rdata=dut.s_axil_rdata,   rresp=dut.s_axil_rresp,     rvalid=dut.s_axil_rvalid,   rready=dut.s_axil_rready,
    )


def _axis_bus(dut) -> AxisSlaveBus:
    return AxisSlaveBus(
        tdata=dut.m_axis_tdata, tkeep=dut.m_axis_tkeep, tvalid=dut.m_axis_tvalid,
        tready=dut.m_axis_tready, tlast=dut.m_axis_tlast,
    )


async def _reset(dut, cycles: int = 6) -> None:
    dut.rst.value = 1
    for sig in ("s_axil_awvalid", "s_axil_wvalid", "s_axil_bready",
                "s_axil_arvalid", "s_axil_rready", "m_axis_tready"):
        getattr(dut, sig).value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def test_top_build_and_run():
    sim = os.environ.get("SIM", "verilator")
    runner = get_runner(sim)
    runner.build(
        sources=[
            str(RTL_DIR / "pktforge_sweep.sv"),
            str(RTL_DIR / "pktforge_regfile_axil.sv"),
            str(RTL_DIR / "pktforge_hdr_builder.sv"),
            str(RTL_DIR / "pktforge_icrc_appender.sv"),
            str(RTL_DIR / "pktforge_fcs_appender.sv"),
            str(RTL_DIR / "pktforge_rate_limiter.sv"),
            str(RTL_DIR / "pktforge_top.sv"),
        ],
        hdl_toplevel="pktforge_top",
        build_dir=str(BUILD_DIR / "top"),
        always=True,
    )
    runner.test(
        hdl_toplevel="pktforge_top",
        test_module="tb.test_top",
        testcase="cocotb_top_full",
    )


async def _write_config(master: AxilMaster, cfg_kwargs) -> None:
    """Push the scenario config to the regfile in the order it's laid out."""
    # ETH MACs (48-bit spanning two 32-bit regs).
    src = cfg_kwargs["eth_src_mac"]
    dst = cfg_kwargs["eth_dst_mac"]
    await master.write(A["ETH_SRC_MAC_LO"], src & 0xFFFFFFFF)
    await master.write(A["ETH_SRC_MAC_HI"], (src >> 32) & 0xFFFF)
    await master.write(A["ETH_DST_MAC_LO"], dst & 0xFFFFFFFF)
    await master.write(A["ETH_DST_MAC_HI"], (dst >> 32) & 0xFFFF)

    await master.write(A["IP_SRC"], cfg_kwargs["ip_src"])
    await master.write(A["IP_DST"], cfg_kwargs["ip_dst"])
    ip_misc = ((cfg_kwargs["ip_ttl"] & 0xFF)
               | ((cfg_kwargs["ip_dscp"] & 0x3F) << 8)
               | ((cfg_kwargs["ip_ecn"] & 0x03) << 14))
    await master.write(A["IP_MISC"], ip_misc)

    tp = (cfg_kwargs["src_port"] & 0xFFFF) | ((cfg_kwargs["dst_port"] & 0xFFFF) << 16)
    await master.write(A["TRANSPORT_PORTS"], tp)

    await master.write(A["ROCE_OP_PKEY"],
                       (cfg_kwargs["opcode"] & 0xFF) | ((cfg_kwargs["pkey"] & 0xFFFF) << 8))
    await master.write(A["ROCE_DEST_QP"], cfg_kwargs["dest_qp"] & 0xFFFFFF)
    await master.write(A["ROCE_PSN_ACK"],
                       (cfg_kwargs["psn_start"] & 0xFFFFFF) | (int(cfg_kwargs["ack_req"]) << 24))

    # Sweeps (only the dims used in these scenarios; leave others at reset).
    sip_min, sip_max, sip_step = cfg_kwargs.get("sweep_sip", (0, 0, 0))
    await master.write(A["SWEEP_SIP_MIN"],  sip_min & 0xFFFFFFFF)
    await master.write(A["SWEEP_SIP_MAX"],  sip_max & 0xFFFFFFFF)
    await master.write(A["SWEEP_SIP_STEP"], sip_step & 0xFFFFFFFF)

    sport_min, sport_max, sport_step = cfg_kwargs.get("sweep_sport", (0, 0, 0))
    await master.write(A["SWEEP_SPORT_MIN"],  sport_min & 0xFFFF)
    await master.write(A["SWEEP_SPORT_MAX"],  sport_max & 0xFFFF)
    await master.write(A["SWEEP_SPORT_STEP"], sport_step & 0xFFFF)

    if cfg_kwargs.get("sweep_size") is not None:
        size_min, size_max, size_step = cfg_kwargs["sweep_size"]
    else:
        size_min, size_max, size_step = cfg_kwargs["size"], cfg_kwargs["size"], 0
    await master.write(A["SWEEP_SIZE_MIN"],  size_min & 0xFFF)
    await master.write(A["SWEEP_SIZE_MAX"],  size_max & 0xFFF)
    await master.write(A["SWEEP_SIZE_STEP"], size_step & 0xFFF)

    # OUTPUT_OPTS bit 0 = FCS enable, bit 1 = ICRC enable. Scenario can
    # override; default is both on to match the regfile reset value.
    output_opts = cfg_kwargs.get("output_opts", 0x3)
    await master.write(A["OUTPUT_OPTS"], output_opts)
    # Rate: defaults are mode=0 (IFG) and IFG=0 (no pacing) so byte-exact
    # scenarios stay quick. Dedicated rate scenarios override.
    await master.write(A["RATE_MODE"],         cfg_kwargs.get("rate_mode", 0))
    await master.write(A["RATE_LINE_PERCENT"], cfg_kwargs.get("rate_line_percent", 100))
    await master.write(A["RATE_IFG_BYTES"],    cfg_kwargs.get("rate_ifg_bytes", 0))
    await master.write(A["PACKET_COUNT"], cfg_kwargs["packet_count"] & 0xFFFFFFFF)


def _mac_str(mac_int: int) -> str:
    return ":".join(f"{(mac_int >> (8 * (5 - i))) & 0xFF:02x}" for i in range(6))


def _ip_str(ip_int: int) -> str:
    return ".".join(str((ip_int >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _sweep_dict(min_, max_, step):
    return None if step == 0 else {"min": min_, "max": max_, "step": step}


def _make_cfg(cfg_kwargs):
    if cfg_kwargs.get("sweep_size") is not None:
        size_dim = {"min": cfg_kwargs["sweep_size"][0],
                    "max": cfg_kwargs["sweep_size"][1],
                    "step": cfg_kwargs["sweep_size"][2]}
    else:
        size_dim = {"min": cfg_kwargs["size"], "max": cfg_kwargs["size"], "step": 1}
    return from_dict({
        "frame_type": "roce_v2",
        "eth": {"src_mac": _mac_str(cfg_kwargs["eth_src_mac"]),
                "dst_mac": _mac_str(cfg_kwargs["eth_dst_mac"]),
                "vlan": None},
        "ip": {"src": _ip_str(cfg_kwargs["ip_src"]),
               "dst": _ip_str(cfg_kwargs["ip_dst"]),
               "ttl": cfg_kwargs["ip_ttl"],
               "dscp": cfg_kwargs["ip_dscp"],
               "ecn": cfg_kwargs["ip_ecn"]},
        "transport": {"src_port": cfg_kwargs["src_port"],
                      "dst_port": cfg_kwargs["dst_port"]},
        "roce": {"opcode": cfg_kwargs["opcode"], "pkey": cfg_kwargs["pkey"],
                 "dest_qp": cfg_kwargs["dest_qp"], "ack_req": cfg_kwargs["ack_req"],
                 "psn_start": cfg_kwargs["psn_start"]},
        "http": None,
        "sweep": {
            "src_ip":   _sweep_dict(*cfg_kwargs.get("sweep_sip", (0, 0, 0))),
            "dst_ip":   None,
            "src_port": _sweep_dict(*cfg_kwargs.get("sweep_sport", (0, 0, 0))),
            "dst_port": None,
            "size":     size_dim,
        },
        "rate": {"mode": "line_percent", "line_percent": 100, "ifg_bytes": None},
        "output": {
            "append_fcs":  bool(cfg_kwargs.get("output_opts", 0x3) & 0x1),
            "append_icrc": bool(cfg_kwargs.get("output_opts", 0x3) & 0x2),
        },
        "run": {"packet_count": cfg_kwargs["packet_count"], "seed": 0},
    })


async def _run_scenario(dut, master: AxilMaster, monitor: AxisSlaveMonitor,
                        cfg_kwargs, *, label: str) -> None:
    await _write_config(master, cfg_kwargs)

    # START pulse (bit 0). RW1S; regfile drives one-cycle ctrl_start_o.
    await master.write(A["CTRL"], 0x1)

    n = cfg_kwargs["packet_count"]
    got = await monitor.recv_n_frames(n, timeout_cycles=100_000)
    cfg = _make_cfg(cfg_kwargs)
    for i, out in enumerate(got):
        expected = frame_bytes(cfg, i)
        assert out == expected, (
            f"[{label}] frame {i}: mismatch\n"
            f"  size expected={len(expected)} got={len(out)}\n"
            f"  expected={expected.hex()}\n"
            f"  got     ={out.hex()}"
        )

    # PACKETS_SENT counter should equal n.
    ps, _ = await master.read(A["PACKETS_SENT"])
    assert ps == n, f"[{label}] PACKETS_SENT expected {n}, got {ps}"


@cocotb.test()
async def cocotb_top_full(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await _reset(dut)

    master = AxilMaster(_axil_bus(dut), dut.clk)
    monitor = AxisSlaveMonitor(dut, _axis_bus(dut), dut.clk, DATA_W, ready_prob=1.0)
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

    # --- 1. Baseline: fixed size=64, 4 packets, both ICRC + FCS on ---
    await _run_scenario(dut, master, monitor, dict(base), label="baseline")

    # --- 2. Larger fixed size ---
    await _reset(dut)
    scen2 = dict(base); scen2["size"] = 128; scen2["packet_count"] = 3
    await _run_scenario(dut, master, monitor, scen2, label="size128")

    # --- 3. Non-aligned size to hit the ICRC/FCS partial-merge paths ---
    await _reset(dut)
    scen3 = dict(base); scen3["size"] = 71; scen3["packet_count"] = 2
    await _run_scenario(dut, master, monitor, scen3, label="size71")

    # --- 4. Source port sweep ---
    await _reset(dut)
    scen4 = dict(base)
    scen4.update(size=80, packet_count=5, sweep_sport=(32768, 32770, 1))
    await _run_scenario(dut, master, monitor, scen4, label="sweep_sport")

    # --- 5. Size sweep ---
    await _reset(dut)
    scen5 = dict(base)
    scen5.update(size=64, packet_count=4, sweep_size=(64, 96, 16))
    await _run_scenario(dut, master, monitor, scen5, label="sweep_size")

    # --- 6. Backpressure ---
    await _reset(dut)
    monitor.ready_prob = 0.4
    monitor.seed(0xB16B00B5)
    scen6 = dict(base); scen6["size"] = 96; scen6["packet_count"] = 3
    await _run_scenario(dut, master, monitor, scen6, label="backpressure")
    monitor.ready_prob = 1.0

    # --- 7-9. Runtime OUTPUT_OPTS bypass matrix ---
    # 0x0 = neither trailer, 0x1 = FCS only, 0x2 = ICRC only
    for opts, name in ((0x0, "no_trailers"), (0x1, "fcs_only"), (0x2, "icrc_only")):
        await _reset(dut)
        scen = dict(base); scen["size"] = 80; scen["packet_count"] = 3
        scen["output_opts"] = opts
        await _run_scenario(dut, master, monitor, scen, label=name)

    # --- 10. Rate-limited (IFG mode): RATE_IFG_BYTES=400 (100 cycles gap per packet) ---
    await _reset(dut)
    scen10 = dict(base); scen10["size"] = 64; scen10["packet_count"] = 3
    scen10["rate_ifg_bytes"] = 400
    await _run_scenario(dut, master, monitor, scen10, label="rate_ifg")

    # --- 11. Rate-limited (LINE_PERCENT mode): 25% of line rate ---
    await _reset(dut)
    scen11 = dict(base); scen11["size"] = 64; scen11["packet_count"] = 3
    scen11["rate_mode"] = 1
    scen11["rate_line_percent"] = 25
    await _run_scenario(dut, master, monitor, scen11, label="rate_line_percent")
