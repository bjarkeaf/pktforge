"""Tests for the RoCEv2 ICRC implementation.

The Phase 0 validation gate is `test_against_scapy_reference`, which
cross-checks `compute_icrc_ipv4` byte-exact against `scapy.contrib.roce`
across a sweep of transports, opcodes, sizes, and header field values.
Scapy's contrib is itself validated against real captures upstream, so a
match here is a practical substitute for local Soft-RoCE captures (which
are unreliable on single-host loopback setups).

`test_against_captures` is kept as an optional stronger test: if you drop
real RoCEv2 pcaps into `model/ref_pcaps/`, it runs; otherwise it skips.
"""

from __future__ import annotations

import random
import zlib
from pathlib import Path

import pytest

from .icrc import (
    DUMMY_LRH_PREFIX,
    compute_icrc_ipv4,
    crc32_ieee,
    mask_bth_header,
    mask_ipv4_header,
    mask_udp_header,
)

REF_PCAP_DIR = Path(__file__).parent / "ref_pcaps"


def test_crc32_matches_zlib():
    payload = b"pktforge" * 16
    assert crc32_ieee(payload) == zlib.crc32(payload) & 0xFFFFFFFF


def test_dummy_prefix_is_eight_ff():
    assert DUMMY_LRH_PREFIX == b"\xff" * 8


def test_ipv4_mask_targets_tos_ttl_and_checksum():
    hdr = bytes(range(20))
    m = mask_ipv4_header(hdr)
    for i in range(20):
        if i in (1, 8, 10, 11):
            assert m[i] == 0xFF, f"byte {i} should be masked"
        else:
            assert m[i] == hdr[i], f"byte {i} should be untouched"


def test_udp_mask_targets_checksum_only():
    hdr = bytes(range(8))
    m = mask_udp_header(hdr)
    for i in range(8):
        if i in (6, 7):
            assert m[i] == 0xFF
        else:
            assert m[i] == hdr[i]


def test_bth_mask_targets_resv8a():
    hdr = bytes(range(12))
    m = mask_bth_header(hdr)
    for i in range(12):
        if i == 4:
            assert m[i] == 0xFF
        else:
            assert m[i] == hdr[i]


def test_masking_helpers_reject_short_inputs():
    with pytest.raises(ValueError):
        mask_ipv4_header(b"\x00" * 19)
    with pytest.raises(ValueError):
        mask_udp_header(b"\x00" * 7)
    with pytest.raises(ValueError):
        mask_bth_header(b"\x00" * 11)


def test_icrc_is_deterministic():
    ip = bytes.fromhex("4500002c00004000401172ba0a0000010a000002")
    udp = bytes.fromhex("80000012b7101770")
    bth = bytes.fromhex("64400000ffff00001000000000000001")[:12]
    payload = b"\x00" * 16
    a = compute_icrc_ipv4(ip, udp, bth, b"", payload)
    b = compute_icrc_ipv4(ip, udp, bth, b"", payload)
    assert a == b
    assert len(a) == 4


def test_against_scapy_reference():
    """Cross-check `compute_icrc_ipv4` against scapy.contrib.roce.

    Sweeps random transports, opcodes, payload sizes, and header field
    values. Any mismatch means our ICRC implementation diverges from a
    battle-tested reference.
    """
    scapy_contrib = pytest.importorskip("scapy.contrib.roce")
    scapy_inet = pytest.importorskip("scapy.layers.inet")
    scapy_l2 = pytest.importorskip("scapy.layers.l2")
    BTH = scapy_contrib.BTH
    opcode = scapy_contrib.opcode
    IP = scapy_inet.IP
    UDP = scapy_inet.UDP
    Ether = scapy_l2.Ether

    ops_by_transport = {
        "UD": ["SEND_ONLY", "SEND_ONLY_WITH_IMMEDIATE"],
        "RC": [
            "SEND_FIRST", "SEND_MIDDLE", "SEND_LAST", "SEND_ONLY",
            "RDMA_WRITE_ONLY", "ACKNOWLEDGE",
        ],
        "UC": ["SEND_ONLY", "RDMA_WRITE_ONLY"],
        "RD": ["SEND_ONLY", "RDMA_WRITE_ONLY", "ACKNOWLEDGE"],
    }
    sizes = [0, 1, 4, 7, 8, 16, 64, 128, 512, 1400]

    rng = random.Random(20260826)
    for _ in range(200):
        transport = rng.choice(list(ops_by_transport))
        op = opcode(transport, rng.choice(ops_by_transport[transport]))[0]
        size = rng.choice(sizes)
        payload = bytes(rng.getrandbits(8) for _ in range(size))
        src = ".".join(str(rng.randint(1, 254)) for _ in range(4))
        dst = ".".join(str(rng.randint(1, 254)) for _ in range(4))
        pkt = (
            Ether()
            / IP(src=src, dst=dst, tos=rng.getrandbits(8), ttl=rng.randint(1, 255))
            / UDP(sport=rng.randint(1024, 65535), dport=4791)
            / BTH(
                opcode=op,
                dqpn=rng.getrandbits(24),
                psn=rng.getrandbits(24),
                fecn=rng.randint(0, 1),
                becn=rng.randint(0, 1),
            )
            / payload
        )
        raw = bytes(pkt)
        ip_hdr = raw[14:34]
        udp_hdr = raw[34:42]
        bth_hdr = raw[42:54]
        tail = raw[54:]
        expected = tail[-4:]
        got = compute_icrc_ipv4(ip_hdr, udp_hdr, bth_hdr, b"", tail[:-4])
        assert got == expected, (
            f"transport={transport} op={op:#x} size={size}: "
            f"expected {expected.hex()}, got {got.hex()}"
        )


def test_against_captures():
    """Round-trip test against captured RoCEv2 frames.

    To enable: drop N pcap files into model/ref_pcaps/, each containing one
    RoCEv2 frame. For each frame, this test extracts headers, recomputes
    the ICRC via `compute_icrc_ipv4`, and asserts it matches the ICRC
    embedded in the captured frame.
    """
    pcaps = sorted(REF_PCAP_DIR.glob("*.pcap")) if REF_PCAP_DIR.exists() else []
    if not pcaps:
        pytest.skip("no reference pcaps present")

    from scapy.all import rdpcap, IP, UDP  # type: ignore[import-untyped]

    for path in pcaps:
        for pkt in rdpcap(str(path)):
            if IP not in pkt or UDP not in pkt:
                continue
            udp_layer = pkt[UDP]
            if int(udp_layer.dport) != 4791:
                continue
            raw = bytes(pkt)
            # Skip Ethernet header (14 bytes, no VLAN in this simple check).
            ip_start = 14
            ip_hdr = raw[ip_start : ip_start + 20]
            udp_start = ip_start + 20
            udp_hdr = raw[udp_start : udp_start + 8]
            bth_start = udp_start + 8
            bth = raw[bth_start : bth_start + 12]
            payload_and_icrc = raw[bth_start + 12 :]
            payload = payload_and_icrc[:-4]
            expected_icrc = payload_and_icrc[-4:]
            got = compute_icrc_ipv4(ip_hdr, udp_hdr, bth, b"", payload)
            assert got == expected_icrc, (
                f"{path.name}: expected {expected_icrc.hex()}, got {got.hex()}"
            )
