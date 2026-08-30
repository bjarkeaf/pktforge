"""Scapy-based golden frame factory for pktforge.

Given a StreamConfig, yields the exact byte sequences the RTL is expected to
produce, packet by packet. The RTL testbench compares its AXI-Stream output
byte-for-byte against this stream.

Sweep semantics: each dim's counter starts at its `min` and advances by
`step` per packet, wrapping to `min` when a step would exceed `max`. When
multiple dims sweep together, they advance independently in lockstep (once
per packet). This mirrors pktgen-dpdk `range` mode.

The RoCEv2 PSN and TCP.seq both increment monotonically per packet and wrap
at their native widths (PSN=24 bits, TCP.seq=32 bits).

FCS is appended when StreamConfig.output.append_fcs is true.
ICRC is appended when StreamConfig.output.append_icrc is true and the frame
type is roce_v2.
"""

from __future__ import annotations

from typing import Iterator

from scapy.all import (  # type: ignore[import-untyped]
    Ether,
    Dot1Q,
    IP,
    UDP,
    TCP,
    Raw,
)

from .config import StreamConfig, SweepDim
from .fcs import compute_fcs
from .icrc import compute_icrc_ipv4


ROCE_UDP_DPORT = 4791
BTH_LEN = 12
PSN_MASK = (1 << 24) - 1
TCP_SEQ_MASK = (1 << 32) - 1


def _mac_str(mac: bytes) -> str:
    return ":".join(f"{b:02x}" for b in mac)


def _ip_str(ip_int: int) -> str:
    return ".".join(str((ip_int >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _sweep_value(dim: SweepDim | None, base: int, packet_index: int) -> int:
    """Return the field value for the Nth packet under a sweep dim, or base."""
    if dim is None:
        return base
    span = (dim.max - dim.min) // dim.step + 1
    return dim.min + (packet_index % span) * dim.step


def _default_size(cfg: StreamConfig) -> int:
    return 128 if cfg.frame_type == "roce_v2" else 256


def _build_bth(cfg: StreamConfig, psn: int) -> bytes:
    """Build the 12-byte BTH header.

    Layout (big-endian on the wire):
      byte 0    : OpCode (8)
      byte 1    : SE(1) | MigReq(1) | PadCnt(2) | TVer(4)
      bytes 2-3 : PKey (16)
      byte 4    : FECN(1) | BECN(1) | Resv6(6)         [masked in ICRC]
      bytes 5-7 : DestQP (24)
      byte 8    : AckReq(1) | Resv7(7)
      bytes 9-11: PSN (24)
    """
    b = bytearray(BTH_LEN)
    b[0] = cfg.roce.opcode & 0xFF
    b[1] = 0
    b[2] = (cfg.roce.pkey >> 8) & 0xFF
    b[3] = cfg.roce.pkey & 0xFF
    b[4] = 0
    b[5] = (cfg.roce.dest_qp >> 16) & 0xFF
    b[6] = (cfg.roce.dest_qp >> 8) & 0xFF
    b[7] = cfg.roce.dest_qp & 0xFF
    b[8] = 0x80 if cfg.roce.ack_req else 0x00
    b[9] = (psn >> 16) & 0xFF
    b[10] = (psn >> 8) & 0xFF
    b[11] = psn & 0xFF
    return bytes(b)


def _eth_layer(cfg: StreamConfig):
    eth = Ether(src=_mac_str(cfg.eth.src_mac), dst=_mac_str(cfg.eth.dst_mac))
    if cfg.eth.vlan is not None:
        eth /= Dot1Q(vlan=cfg.eth.vlan.id, prio=cfg.eth.vlan.pcp)
    return eth


def _ipv4_layer(cfg: StreamConfig, src_ip: int, dst_ip: int):
    tos = ((cfg.ip.dscp & 0x3F) << 2) | (cfg.ip.ecn & 0x03)
    return IP(
        src=_ip_str(src_ip),
        dst=_ip_str(dst_ip),
        ttl=cfg.ip.ttl,
        tos=tos,
        flags="DF",
    )


def _frame_roce(cfg, idx, src_ip, dst_ip, src_port, dst_port, size):
    psn = (cfg.roce.psn_start + idx) & PSN_MASK
    bth = _build_bth(cfg, psn)

    icrc_len = 4 if cfg.output.append_icrc else 0
    fcs_len = 4 if cfg.output.append_fcs else 0

    eth = _eth_layer(cfg)
    eth_hdr_len = 14 + (4 if cfg.eth.vlan is not None else 0)
    ip_hdr_len = 20
    udp_hdr_len = 8
    payload_len = size - eth_hdr_len - ip_hdr_len - udp_hdr_len - BTH_LEN - icrc_len - fcs_len
    if payload_len < 0:
        payload_len = 0

    seed = (psn.to_bytes(4, "big") + b"pktforge").ljust(16, b"\x00")
    reps = (payload_len + 15) // 16
    payload = (seed * reps)[:payload_len]

    ip = _ipv4_layer(cfg, src_ip, dst_ip)
    # IB Annex A17.4.5.3: UDP checksum SHOULD be zero on transmit for RoCEv2;
    # integrity is provided by the ICRC. Real RoCE NICs (Mellanox, BlueField)
    # emit chksum=0 unconditionally. Force it here rather than let scapy compute
    # a normal UDP checksum, so RTL doesn't have to carry a UDP pseudo-header
    # adder that no compliant RoCE receiver would validate anyway.
    udp = UDP(sport=src_port, dport=dst_port, chksum=0)
    # Include a zero-filled ICRC placeholder so scapy sets IP.len and UDP.len
    # to the final on-wire values. We overwrite the placeholder with the real
    # ICRC once we have the built header bytes.
    tail_placeholder = bth + payload + (b"\x00" * icrc_len)
    full = eth / ip / udp / Raw(load=tail_placeholder)
    built = bytes(full)

    if cfg.output.append_icrc:
        ip_start = eth_hdr_len
        udp_start = ip_start + ip_hdr_len
        ip_hdr_bytes = built[ip_start : ip_start + ip_hdr_len]
        udp_hdr_bytes = built[udp_start : udp_start + udp_hdr_len]
        icrc = compute_icrc_ipv4(ip_hdr_bytes, udp_hdr_bytes, bth, b"", payload)
        frame_no_fcs = built[:-4] + icrc
    else:
        frame_no_fcs = built

    if cfg.output.append_fcs:
        return frame_no_fcs + compute_fcs(frame_no_fcs)
    return frame_no_fcs


def _frame_tcp_http(cfg, idx, src_ip, dst_ip, src_port, dst_port, size):
    tcp_seq = (cfg.http.tcp_seq_start + idx) & TCP_SEQ_MASK
    fcs_len = 4 if cfg.output.append_fcs else 0

    eth = _eth_layer(cfg)
    eth_hdr_len = 14 + (4 if cfg.eth.vlan is not None else 0)
    ip_hdr_len = 20
    tcp_hdr_len = 20
    payload_room = size - eth_hdr_len - ip_hdr_len - tcp_hdr_len - fcs_len
    if payload_room < 0:
        payload_room = 0

    body = cfg.http.payload_template.format(seq=tcp_seq).encode("ascii")
    if len(body) >= payload_room:
        payload = body[:payload_room]
    else:
        payload = body + (b"\x00" * (payload_room - len(body)))

    flags = cfg.http.tcp_flags
    ip = _ipv4_layer(cfg, src_ip, dst_ip)
    tcp = TCP(
        sport=src_port,
        dport=dst_port,
        seq=tcp_seq,
        ack=cfg.http.tcp_ack,
        window=cfg.http.tcp_window,
        flags=flags,
    )
    full = eth / ip / tcp / Raw(load=payload)
    frame_no_fcs = bytes(full)
    if cfg.output.append_fcs:
        return frame_no_fcs + compute_fcs(frame_no_fcs)
    return frame_no_fcs


def frame_bytes(cfg: StreamConfig, packet_index: int) -> bytes:
    """Return the on-wire bytes for the Nth packet (N=0 based) of a stream."""
    src_ip = _sweep_value(cfg.sweep.src_ip, cfg.ip.src, packet_index)
    dst_ip = _sweep_value(cfg.sweep.dst_ip, cfg.ip.dst, packet_index)
    src_port = _sweep_value(cfg.sweep.src_port, cfg.transport.src_port, packet_index)
    dst_port = _sweep_value(cfg.sweep.dst_port, cfg.transport.dst_port, packet_index)
    size = _sweep_value(cfg.sweep.size, _default_size(cfg), packet_index)

    if cfg.frame_type == "roce_v2":
        return _frame_roce(cfg, packet_index, src_ip, dst_ip, src_port, dst_port, size)
    return _frame_tcp_http(cfg, packet_index, src_ip, dst_ip, src_port, dst_port, size)


def frames(cfg: StreamConfig) -> Iterator[bytes]:
    """Yield frame bytes for the full stream (respecting cfg.run.packet_count)."""
    count = cfg.run.packet_count or 0
    i = 0
    while count == 0 or i < count:
        yield frame_bytes(cfg, i)
        i += 1
