"""RoCEv2 Invariant CRC (ICRC) implementation.

Reference: InfiniBand Architecture Specification, Volume 1, Annex A17
(RoCEv2). The ICRC is a CRC-32 over a "pseudo-packet" containing the RoCEv2
frame with a set of invariant fields masked to 0xFF and an 8-byte 0xFF
prefix (a placeholder for the LRH that is absent in RoCEv2).

STATUS: **UNVALIDATED**. This implementation follows the spec as best we
understand it but has not yet been checked byte-exact against real Soft-RoCE
captures. That validation is Phase 0 of pktforge and is tracked in
docs/status.md. Until it is validated, `test_icrc.py::test_against_captures`
is xfail. Do not port this to RTL until validation passes.

CRC-32 parameters (matches Ethernet FCS and zlib.crc32):
    poly=0x04C11DB7, init=0xFFFFFFFF, refin=True, refout=True, xorout=0xFFFFFFFF

Byte-level masking rules for the pseudo-packet, IPv4 case:
    Prefix (8 bytes): 0xFF * 8                 (dummy LRH)
    IPv4[1]         : 0xFF                     (ToS: DSCP+ECN)
    IPv4[8]         : 0xFF                     (TTL)
    IPv4[10:12]     : 0xFF 0xFF                (Header Checksum)
    UDP[6:8]        : 0xFF 0xFF                (UDP Checksum)
    BTH[4]          : 0xFF                     (FECN+BECN+Resv6 aka Resv8a)

IPv6 case: prefix is still 8 bytes of 0xFF, and IPv6 fields are masked per
Annex A17 (Traffic Class, Hop Limit). Not implemented yet — assert on entry.
"""

from __future__ import annotations

import zlib

DUMMY_LRH_PREFIX = b"\xff" * 8


def crc32_ieee(data: bytes) -> int:
    """CRC-32 with the standard Ethernet parameters. Returns a 32-bit int."""
    return zlib.crc32(data) & 0xFFFFFFFF


def mask_ipv4_header(ipv4: bytes) -> bytes:
    """Return the 20+ byte IPv4 header with ICRC-invariant fields set to 0xFF."""
    if len(ipv4) < 20:
        raise ValueError("ipv4 header shorter than 20 bytes")
    out = bytearray(ipv4)
    out[1] = 0xFF          # ToS (DSCP | ECN)
    out[8] = 0xFF          # TTL
    out[10] = 0xFF         # Header Checksum high
    out[11] = 0xFF         # Header Checksum low
    return bytes(out)


def mask_udp_header(udp: bytes) -> bytes:
    """Return the 8-byte UDP header with the Checksum field set to 0xFF."""
    if len(udp) != 8:
        raise ValueError("udp header must be 8 bytes")
    out = bytearray(udp)
    out[6] = 0xFF          # Checksum high
    out[7] = 0xFF          # Checksum low
    return bytes(out)


def mask_bth_header(bth: bytes) -> bytes:
    """Return the 12-byte BTH with the FECN+BECN+Resv6 byte set to 0xFF."""
    if len(bth) != 12:
        raise ValueError("bth header must be 12 bytes")
    out = bytearray(bth)
    out[4] = 0xFF          # FECN | BECN | Resv6 (Annex A17 "Resv8a")
    return bytes(out)


def build_pseudo_packet_ipv4(
    ipv4_hdr: bytes,
    udp_hdr: bytes,
    bth_hdr: bytes,
    ext_hdrs: bytes,
    payload: bytes,
) -> bytes:
    """Assemble the ICRC pseudo-packet for a RoCEv2/IPv4 frame."""
    return (
        DUMMY_LRH_PREFIX
        + mask_ipv4_header(ipv4_hdr)
        + mask_udp_header(udp_hdr)
        + mask_bth_header(bth_hdr)
        + ext_hdrs
        + payload
    )


def compute_icrc_ipv4(
    ipv4_hdr: bytes,
    udp_hdr: bytes,
    bth_hdr: bytes,
    ext_hdrs: bytes = b"",
    payload: bytes = b"",
) -> bytes:
    """Compute the 4-byte ICRC field for a RoCEv2 IPv4 packet.

    Returns the ICRC in the byte order that goes on the wire (LSB first).
    """
    pseudo = build_pseudo_packet_ipv4(ipv4_hdr, udp_hdr, bth_hdr, ext_hdrs, payload)
    crc = crc32_ieee(pseudo)
    # IB spec places the ICRC in little-endian order at the end of the packet.
    return crc.to_bytes(4, "little")


__all__ = [
    "DUMMY_LRH_PREFIX",
    "crc32_ieee",
    "mask_ipv4_header",
    "mask_udp_header",
    "mask_bth_header",
    "build_pseudo_packet_ipv4",
    "compute_icrc_ipv4",
]
