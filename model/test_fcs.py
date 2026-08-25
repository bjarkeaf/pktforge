"""Tests for the FCS helper."""

from __future__ import annotations

import zlib

from .fcs import append_fcs, check_fcs, compute_fcs


def test_zero_frame_matches_zlib():
    payload = b"\x00" * 60
    expected = (zlib.crc32(payload) & 0xFFFFFFFF).to_bytes(4, "little")
    assert compute_fcs(payload) == expected


def test_roundtrip():
    frame = bytes(range(64))
    with_fcs = append_fcs(frame)
    assert len(with_fcs) == len(frame) + 4
    assert check_fcs(with_fcs)


def test_detects_tamper():
    frame = bytes(range(64))
    with_fcs = bytearray(append_fcs(frame))
    with_fcs[10] ^= 0x01
    assert not check_fcs(bytes(with_fcs))
