"""Shared pytest fixtures for pktforge cocotb runners.

We use the modern cocotb runner API (cocotb.runner.get_runner) instead of
per-test cocotb Makefiles so every test is a plain pytest function that
builds and runs its own simulation.

Simulator: Verilator, configured via the SIM env var (default 'verilator').
Set SIM=icarus if Verilator is unavailable on a given host.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import pytest

REPO = Path(__file__).resolve().parents[1]
RTL_DIR = REPO / "rtl"
BUILD_DIR = REPO / "tb" / "sim_build"


def _sim() -> str:
    return os.environ.get("SIM", "verilator")


@pytest.fixture
def sim_name() -> str:
    return _sim()


@pytest.fixture
def rtl_dir() -> Path:
    return RTL_DIR


@pytest.fixture
def build_dir() -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    return BUILD_DIR


def rtl_files(names: Iterable[str]) -> list[str]:
    return [str(RTL_DIR / n) for n in names]
