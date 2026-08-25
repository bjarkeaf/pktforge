"""Ethernet FCS (CRC-32/IEEE 802.3) helper.

The FCS is appended after the L2 payload. It covers everything from the
destination MAC through the end of the payload, but excludes the preamble,
SFD, and the FCS field itself.

Uses the standard CRC-32 parameters (matches zlib.crc32 and binascii.crc32):
    poly=0x04C11DB7, init=0xFFFFFFFF, refin=True, refout=True, xorout=0xFFFFFFFF

On the wire, the FCS is transmitted MSB last per octet, LSB byte first at the
byte level. In captured pcaps (and here) the FCS is stored little-endian:
LSB of the CRC as the first byte after the payload.
"""

from __future__ import annotations

import zlib


def compute_fcs(frame_without_fcs: bytes) -> bytes:
    """Compute the 4-byte FCS for an L2 frame and return it in wire byte order."""
    crc = zlib.crc32(frame_without_fcs) & 0xFFFFFFFF
    return crc.to_bytes(4, "little")


def append_fcs(frame_without_fcs: bytes) -> bytes:
    """Return frame with the FCS appended."""
    return frame_without_fcs + compute_fcs(frame_without_fcs)


def check_fcs(frame_with_fcs: bytes) -> bool:
    """Verify a frame's trailing FCS."""
    if len(frame_with_fcs) < 4:
        return False
    body, expected = frame_with_fcs[:-4], frame_with_fcs[-4:]
    return compute_fcs(body) == expected
