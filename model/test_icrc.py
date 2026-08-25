"""Tests for the RoCEv2 ICRC implementation.

`test_against_captures` is xfail until docs/status.md says the model has
been validated byte-exact against real Soft-RoCE captures. That is the
Phase 0 exit criterion. The other tests exercise the individual masking
helpers and the CRC-32 wrapper.
"""

from __future__ import annotations

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


@pytest.mark.xfail(
    reason="ICRC not yet validated against real Soft-RoCE captures; see docs/status.md",
    strict=False,
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
