"""Config loader and normalization for pktforge.

Reads a YAML config matching model/config_schema.yaml and returns a normalized
dataclass tree. The RTL AXI-Lite register file and the golden model both
consume this same normalized form so field width, byte order, and default
handling stay in sync.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


def _mac_to_bytes(mac: str) -> bytes:
    parts = mac.split(":")
    if len(parts) != 6:
        raise ValueError(f"bad mac: {mac!r}")
    return bytes(int(p, 16) for p in parts)


def _ip_to_int(ip: str) -> int:
    return int(ipaddress.IPv4Address(ip))


@dataclass
class SweepDim:
    min: int
    max: int
    step: int

    def next(self, current: int) -> int:
        nxt = current + self.step
        if nxt > self.max:
            return self.min
        return nxt


@dataclass
class VlanCfg:
    id: int
    pcp: int = 0
    dei: int = 0


@dataclass
class EthCfg:
    src_mac: bytes
    dst_mac: bytes
    vlan: Optional[VlanCfg] = None


@dataclass
class IpCfg:
    src: int
    dst: int
    ttl: int = 64
    dscp: int = 0
    ecn: int = 0


@dataclass
class TransportCfg:
    src_port: int
    dst_port: int


@dataclass
class RoceCfg:
    opcode: int
    pkey: int
    dest_qp: int
    ack_req: bool
    psn_start: int


@dataclass
class HttpCfg:
    payload_template: str
    tcp_seq_start: int
    tcp_ack: int
    tcp_window: int
    tcp_flags: str


@dataclass
class SweepCfg:
    src_ip: Optional[SweepDim] = None
    dst_ip: Optional[SweepDim] = None
    src_port: Optional[SweepDim] = None
    dst_port: Optional[SweepDim] = None
    size: Optional[SweepDim] = None


@dataclass
class RateCfg:
    mode: str
    line_percent: Optional[int] = None
    ifg_bytes: Optional[int] = None


@dataclass
class OutputCfg:
    append_fcs: bool = True
    append_icrc: bool = True


@dataclass
class RunCfg:
    packet_count: int = 0
    seed: int = 0


@dataclass
class StreamConfig:
    frame_type: str
    eth: EthCfg
    ip: IpCfg
    transport: TransportCfg
    roce: Optional[RoceCfg]
    http: Optional[HttpCfg]
    sweep: SweepCfg
    rate: RateCfg
    output: OutputCfg
    run: RunCfg


def _sweep_dim(raw, kind: str) -> Optional[SweepDim]:
    if raw is None:
        return None
    if kind == "ip":
        return SweepDim(_ip_to_int(raw["min"]), _ip_to_int(raw["max"]), int(raw["step"]))
    return SweepDim(int(raw["min"]), int(raw["max"]), int(raw["step"]))


def load_config(path: str | Path) -> StreamConfig:
    with open(path) as fh:
        raw = yaml.safe_load(fh)
    return from_dict(raw)


def from_dict(raw: dict) -> StreamConfig:
    ft = raw["frame_type"]
    if ft not in ("roce_v2", "tcp_http"):
        raise ValueError(f"unknown frame_type {ft!r}")

    vlan_raw = raw["eth"].get("vlan")
    vlan = VlanCfg(**vlan_raw) if vlan_raw else None

    eth = EthCfg(
        src_mac=_mac_to_bytes(raw["eth"]["src_mac"]),
        dst_mac=_mac_to_bytes(raw["eth"]["dst_mac"]),
        vlan=vlan,
    )
    ip = IpCfg(
        src=_ip_to_int(raw["ip"]["src"]),
        dst=_ip_to_int(raw["ip"]["dst"]),
        ttl=int(raw["ip"].get("ttl", 64)),
        dscp=int(raw["ip"].get("dscp", 0)),
        ecn=int(raw["ip"].get("ecn", 0)),
    )
    transport = TransportCfg(
        src_port=int(raw["transport"]["src_port"]),
        dst_port=int(raw["transport"]["dst_port"]),
    )
    roce = None
    if ft == "roce_v2":
        r = raw["roce"]
        roce = RoceCfg(
            opcode=int(r["opcode"]),
            pkey=int(r["pkey"]),
            dest_qp=int(r["dest_qp"]),
            ack_req=bool(r["ack_req"]),
            psn_start=int(r["psn_start"]),
        )
    http = None
    if ft == "tcp_http":
        h = raw["http"]
        http = HttpCfg(
            payload_template=h["payload_template"],
            tcp_seq_start=int(h["tcp_seq_start"]),
            tcp_ack=int(h["tcp_ack"]),
            tcp_window=int(h["tcp_window"]),
            tcp_flags=h["tcp_flags"],
        )
    sweep = SweepCfg(
        src_ip=_sweep_dim(raw["sweep"].get("src_ip"), "ip"),
        dst_ip=_sweep_dim(raw["sweep"].get("dst_ip"), "ip"),
        src_port=_sweep_dim(raw["sweep"].get("src_port"), "int"),
        dst_port=_sweep_dim(raw["sweep"].get("dst_port"), "int"),
        size=_sweep_dim(raw["sweep"].get("size"), "int"),
    )
    rate = RateCfg(
        mode=raw["rate"]["mode"],
        line_percent=raw["rate"].get("line_percent"),
        ifg_bytes=raw["rate"].get("ifg_bytes"),
    )
    output = OutputCfg(
        append_fcs=bool(raw["output"].get("append_fcs", True)),
        append_icrc=bool(raw["output"].get("append_icrc", True)),
    )
    run = RunCfg(
        packet_count=int(raw["run"].get("packet_count", 0)),
        seed=int(raw["run"].get("seed", 0)),
    )
    return StreamConfig(
        frame_type=ft,
        eth=eth,
        ip=ip,
        transport=transport,
        roce=roce,
        http=http,
        sweep=sweep,
        rate=rate,
        output=output,
        run=run,
    )
