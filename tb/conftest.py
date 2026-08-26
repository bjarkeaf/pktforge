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

# Do not use Path.resolve(): it follows symlinks and defeats the
# space-in-path workaround (users can symlink the repo under /tmp/pktforge
# to sidestep Verilator's GNU-Make-cannot-handle-spaces limitation).
REPO = Path(__file__).absolute().parents[1]
RTL_DIR = REPO / "rtl"

# Verilator's Make refuses to build in directories that contain spaces.
# Redirect to /tmp when the repo path has spaces or if the user overrides.
_default_build = REPO / "tb" / "sim_build"
if " " in str(_default_build):
    BUILD_DIR = Path("/tmp") / "pktforge_sim_build"
else:
    BUILD_DIR = _default_build
BUILD_DIR = Path(os.environ.get("PKTFORGE_SIM_BUILD", BUILD_DIR))


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
