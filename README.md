# pktforge

Open-source, stateless FPGA packet generator IP core. Vendor-neutral SystemVerilog, AXI-Stream output.

## Status

**RoCEv2 simulation MVP complete.** `pktforge_top` composes the regfile, header/payload builder, ICRC and FCS appenders, and rate limiter behind one AXI-Lite subordinate and one AXI-Stream master. Every emitted frame is diffed byte-exact against a Scapy golden model in CI. See `docs/status.md` for what's done and what's next; board bring-up on the DE10-Nano is the outstanding item.

## What it does

`pktforge_top` generates network frames at line rate from an AXI-Stream master port, driven by an AXI-Lite register file (register map in [`docs/regmap.md`](docs/regmap.md)). Supported today:

- Ethernet + IPv4 + UDP + RoCEv2 (BTH header, per-packet PSN, optional ICRC)
- Optional Ethernet FCS trailer
- Sweep engine over src/dst IP, src/dst port, and frame size (matches pktgen-dpdk `range` mode) to spread across NIC receive queues
- Rate limiter with IFG-bytes mode and LINE_PERCENT mode

Planned but not yet in RTL: TCP+HTTP frame type (the Python golden model already supports it) and VLAN insertion. A `pktforge_checker` companion core for on-hardware loopback verification is on the roadmap after board bring-up.

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
