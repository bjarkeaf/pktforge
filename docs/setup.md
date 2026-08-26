# pktforge — local setup guide

You need three groups of tools: (1) Python packages for the golden model and cocotb, (2) an HDL simulator, (3) a vendor toolchain for eventual synthesis and board bring-up. Item 3 is only needed when you start on-hardware work.

The commands below assume Arch Linux; adapt package names for your distro.

## 1. Python packages

```bash
sudo pacman -S python-scapy python-yaml python-pytest python-cocotb
```

`python-cocotb-bus` may not be packaged; if `make test` complains about `cocotb_bus`, install it via pip in a venv:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r model/requirements.txt -r tb/requirements.txt
```

The `.venv/` path is gitignored. Reactivate with `source .venv/bin/activate` in future sessions, or add an alias.

Sanity check (works with or without a venv):

```bash
make golden-check
```

Expected: all Python tests pass or skip cleanly. The `test_against_captures` case skips until reference pcaps land in `model/ref_pcaps/` (see [Soft-RoCE captures](#soft-roce-captures-for-icrc-validation)).

## 2. HDL simulator

Verilator is the target simulator for CI and for local iteration.

```bash
sudo pacman -S verilator gtkwave
```

Note that cocotb 2.0+ requires a recent Verilator (≥ 5.028) for its runner-driven flow. If your distro ships an older version, build from source or use `oss-cad-suite`.

**Path caveat:** Verilator's generated Makefile does not tolerate spaces in the source or build paths (a GNU Make limitation). If the repo lives at a path with spaces, `make test` fails with `No rule to make target '/home/.../first-word'`. Fix: clone the repo somewhere without spaces or symlink it (e.g. `ln -s '/orig/with space/pktforge' /tmp/pktforge`) and operate on the symlinked path (including a venv installed there). CI has no such issue.

Sanity check:

```bash
verilator --version
make lint             # runs verilator lint-only on rtl/
make test             # runs cocotb suites; requires cocotb from step 1
```

If you prefer Icarus for a specific test, set `SIM=icarus` in the environment.

## 3. Vendor toolchain (bring-up only)

Only needed when you start on-hardware bring-up. Skip until then. The RTL is vendor-neutral, so you can target Intel/Altera (Quartus), AMD/Xilinx (Vivado), or Lattice (Radiant/nextpnr) — pick whatever matches your board.

Sanity check after install:

```bash
quartus_sh --version   # or vivado -version, etc.
```

If your board has a JTAG programmer with its own udev rules, remember to replug after driver install and add yourself to the relevant group (`plugdev` on many distros).

## Soft-RoCE captures for ICRC validation (optional)

The Python ICRC implementation in `model/icrc.py` is cross-validated byte-exact against `scapy.contrib.roce` over a 200-shape random sweep. That is the Phase 0 exit gate and is checked by `test_against_scapy_reference`, which runs as part of `make golden-check`.

Validating against **real** Soft-RoCE captures is a stronger check but requires a two-host setup: single-host loopback with `rdma_rxe` bypasses the netdev, so tcpdump captures zero packets. If you have a second Linux box, the steps below produce reference pcaps that `test_against_captures` will pick up automatically.

Steps (two hosts required):

```bash
# One-shot per boot: enable Soft-RoCE on the loopback interface.
sudo modprobe rdma_rxe
sudo rdma link add rxe0 type rxe netdev lo

# Terminal A: capture
sudo tcpdump -i lo -w model/ref_pcaps/rocev2-udX.pcap udp port 4791

# Terminal B: generate a bit of traffic. Requires rdma-core.
sudo pacman -S rdma-core   # if not already installed
ib_send_bw -d rxe0 -F --report_gbits &
sleep 1
ib_send_bw -d rxe0 -F --report_gbits localhost
```

Capture several frames of different sizes. Save each as `model/ref_pcaps/*.pcap` and run:

```bash
python -m pytest model/test_icrc.py::test_against_captures -v
```

If it passes, record the pcap filenames in `docs/status.md` and note in `model/icrc.py`'s header that real-capture validation is now complete on top of the Scapy cross-check.

## Troubleshooting

- **`ModuleNotFoundError: cocotb`** — install `python-cocotb` from pacman, or activate the venv
- **`verilator: command not found`** — install `verilator` from pacman
- **`ib_send_bw: command not found`** — install `rdma-core` from pacman
- **`rdma link add: RTNETLINK answers: File exists`** — the link is already up, ignore
- **Cocotb build errors mentioning `clearEvalNeeded` / `doInertialPuts`** — your Verilator is older than 5.028; upgrade
