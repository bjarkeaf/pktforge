"""Tests for the config loader (no external deps beyond pyyaml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .config import from_dict, load_config

MIN_ROCE = {
    "frame_type": "roce_v2",
    "eth": {"src_mac": "02:00:00:00:00:01", "dst_mac": "02:00:00:00:00:02", "vlan": None},
    "ip": {"src": "10.0.0.1", "dst": "10.0.0.2", "ttl": 64, "dscp": 0, "ecn": 0},
    "transport": {"src_port": 32768, "dst_port": 4791},
    "roce": {
        "opcode": 0x64,
        "pkey": 0xFFFF,
        "dest_qp": 0x10,
        "ack_req": False,
        "psn_start": 0,
    },
    "http": None,
    "sweep": {
        "src_ip": None,
        "dst_ip": None,
        "src_port": {"min": 32768, "max": 32771, "step": 1},
        "dst_port": None,
        "size": None,
    },
    "rate": {"mode": "line_percent", "line_percent": 100, "ifg_bytes": None},
    "output": {"append_fcs": True, "append_icrc": True},
    "run": {"packet_count": 10, "seed": 0},
}


def test_loads_roce():
    cfg = from_dict(MIN_ROCE)
    assert cfg.frame_type == "roce_v2"
    assert cfg.eth.src_mac == bytes.fromhex("020000000001")
    assert cfg.eth.dst_mac == bytes.fromhex("020000000002")
    assert cfg.ip.src == 0x0A000001
    assert cfg.ip.dst == 0x0A000002
    assert cfg.transport.dst_port == 4791
    assert cfg.roce is not None
    assert cfg.roce.opcode == 0x64
    assert cfg.sweep.src_port is not None
    assert cfg.sweep.src_port.min == 32768
    assert cfg.output.append_fcs is True


def test_sweep_dim_wraps():
    cfg = from_dict(MIN_ROCE)
    dim = cfg.sweep.src_port
    assert dim is not None
    assert dim.next(dim.max) == dim.min


def test_rejects_bad_frame_type():
    bad = dict(MIN_ROCE)
    bad["frame_type"] = "nonsense"
    with pytest.raises(ValueError):
        from_dict(bad)


def test_loads_from_schema_yaml():
    schema = Path(__file__).parent / "config_schema.yaml"
    cfg = load_config(schema)
    assert cfg.frame_type == "roce_v2"
    assert cfg.transport.dst_port == 4791
