# pktforge

Open-source, stateless FPGA packet generator IP core. Vendor-neutral SystemVerilog, AXI-Stream output.

## Status

**Pre-alpha, scaffolding.** No RTL yet. See `docs/status.md` for the current session state.

## What it does

`pktforge_core` generates network frames at line rate from an AXI-Stream master port, driven by an AXI-Lite register file. Frame types:

- Ethernet + IPv4 + UDP + RoCEv2 (BTH header, per-packet PSN, optional ICRC)
- Ethernet + IPv4 + TCP with HTTP-shaped payload (stateless incrementing sequence numbers)

A range/sweep engine varies src/dst IP, src/dst port, and frame size to spread across NIC receive queues (RSS).

A companion `pktforge_checker` core consumes AXI-Stream and mirror-compares against the same configuration for on-hardware verification via internal fabric loopback.

## Why it exists

Line-rate hashing pipelines (RoCEv2 for RDMA fabrics, TCP+HTTP for datacenter traffic) need reproducible synthetic traffic to develop and verify against. A soft IP core lets you generate that traffic on the same FPGA that's running the pipeline under test, without a separate server pushing packets over the wire. The behavioural spec is modelled on `pktgen-dpdk`, so anyone already benchmarking with pktgen has an equivalent knob set.

## Verification tiers

1. **Simulation**: cocotb + Verilator, byte-exact diff against a Scapy golden model, on every commit
2. **Board loopback**: `pktforge_core` → `pktforge_checker` in fabric, ≥1M frames zero mismatches
3. **Real cable (stretch)**: 1GbE out to a host, tcpdump capture diffed against the same golden model

## Repo layout

```
rtl/     synthesizable SystemVerilog
model/   Python golden model (Scapy + hand-rolled ICRC)
tb/      cocotb testbenches
synth/   vendor project files (human-run)
docs/    integration guide, register map, session status
```

## Getting started

See [`docs/setup.md`](docs/setup.md) for tool installation.

```
make test         # run all cocotb suites via Verilator
make test MOD=X   # run one module suite
make lint         # verilator lint on rtl/
```

## License

Apache-2.0. See `LICENSE`.
