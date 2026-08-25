"""Tests for the Scapy golden frame factory.

Requires scapy. Skipped cleanly when scapy is not installed so the rest of
the model self-tests remain runnable during quick local iteration.
"""

from __future__ import annotations

import pytest

pytest.importorskip("scapy", reason="scapy not installed; see docs/setup.md")

from scapy.all import Ether, IP, UDP, TCP  # noqa: E402

from .config import from_dict  # noqa: E402
from .fcs import check_fcs  # noqa: E402
from .test_config import MIN_ROCE  # noqa: E402


def _make_tcp_cfg():
    cfg = dict(MIN_ROCE)
    cfg["frame_type"] = "tcp_http"
    cfg["transport"] = {"src_port": 32768, "dst_port": 80}
    cfg["roce"] = None
    cfg["http"] = {
        "payload_template": "GET /hash-test?seq={seq} HTTP/1.1\r\nHost: pktforge\r\n\r\n",
        "tcp_seq_start": 0,
        "tcp_ack": 0,
        "tcp_window": 65535,
        "tcp_flags": "PA",
    }
    cfg["sweep"] = {
        "src_ip": None,
        "dst_ip": None,
        "src_port": None,
        "dst_port": None,
        "size": None,
    }
    cfg["run"] = {"packet_count": 5, "seed": 0}
    cfg["output"] = {"append_fcs": True, "append_icrc": False}
    return cfg


def test_roce_frame_fcs_verifies():
    from .golden import frame_bytes
    cfg = from_dict(MIN_ROCE)
    for i in range(3):
        raw = frame_bytes(cfg, i)
        assert check_fcs(raw), f"packet {i} FCS mismatch"


def test_roce_frame_has_ipv4_and_udp():
    from .golden import frame_bytes
    cfg = from_dict(MIN_ROCE)
    raw = frame_bytes(cfg, 0)
    # Strip FCS before feeding to scapy so its own checksum check passes.
    parsed = Ether(raw[:-4])
    assert IP in parsed
    assert UDP in parsed
    assert int(parsed[UDP].dport) == 4791


def test_roce_psn_increments_per_packet():
    from .golden import frame_bytes
    cfg = from_dict(MIN_ROCE)
    psns = []
    for i in range(4):
        raw = frame_bytes(cfg, i)
        parsed = Ether(raw[:-4])
        udp_end = 14 + 20 + 8
        bth = bytes(raw)[udp_end : udp_end + 12]
        psn = (bth[9] << 16) | (bth[10] << 8) | bth[11]
        psns.append(psn)
    assert psns == [0, 1, 2, 3]


def test_roce_src_port_sweep_advances():
    from .golden import frame_bytes
    cfg = from_dict(MIN_ROCE)  # sweep: src_port min=32768 max=32771 step=1
    ports = []
    for i in range(5):
        raw = frame_bytes(cfg, i)
        parsed = Ether(raw[:-4])
        ports.append(int(parsed[UDP].sport))
    assert ports == [32768, 32769, 32770, 32771, 32768]


def test_tcp_http_frame_parses_and_payload_contains_seq():
    from .golden import frame_bytes
    cfg = from_dict(_make_tcp_cfg())
    raw = frame_bytes(cfg, 3)
    assert check_fcs(raw)
    parsed = Ether(raw[:-4])
    assert TCP in parsed
    payload = bytes(parsed[TCP].payload)
    assert b"GET /hash-test?seq=" in payload


def test_size_sweep_respected():
    from .golden import frame_bytes
    cfg_dict = dict(MIN_ROCE)
    cfg_dict["sweep"] = dict(cfg_dict["sweep"])
    cfg_dict["sweep"]["size"] = {"min": 128, "max": 256, "step": 64}
    cfg = from_dict(cfg_dict)
    sizes = [len(frame_bytes(cfg, i)) for i in range(4)]
    assert sizes == [128, 192, 256, 128]
