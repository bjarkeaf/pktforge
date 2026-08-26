# Session status

Handoff document. Update at the end of every session. Read at the start of every session before touching anything.

## Current state

**Phase 1 in progress.** Phase 0 exit gate cleared (ICRC cross-validated). First real RTL module `pktforge_regfile_axil.sv` implemented with full cocotb test coverage against `docs/regmap.md`. Passthrough smoke test superseded and removed.

## What is done

- Repo scaffold: LICENSE (Apache-2.0), `.gitignore`, `README.md`, `Makefile`, `.github/workflows/ci.yml`
- Config loader (`model/config.py`) with YAML schema (`model/config_schema.yaml`), tested
- Ethernet FCS helper (`model/fcs.py`), tested
- RoCEv2 ICRC implementation (`model/icrc.py`) with masking helpers and unit tests. Cross-validated byte-exact against `scapy.contrib.roce` over a 200-shape random sweep (`test_against_scapy_reference`). Real-capture validation remains available as opt-in but is not required for Phase 0 exit
- Scapy golden-model frame factory (`model/golden.py`) for RoCEv2 and TCP+HTTP, with sweep-per-packet, PSN/seq incrementing, FCS/ICRC appending
- cocotb harness: `tb/conftest.py`, `tb/lib/axis.py` (AXI-Stream master driver + slave monitor with tkeep/backpressure), `tb/lib/axil.py` (AXI-Lite master BFM)
- `rtl/pktforge_regfile_axil.sv`: AXI-Lite register file per `docs/regmap.md`. 32-bit data, 8-bit address, per-byte wstrb, RW1S CTRL pulses, RO ID/VERSION/STATUS/PACKETS_SENT, SLVERR on reserved/unaligned. Fully tested by `tb/test_regfile.py`
- CI: sim job enabled, pulls oss-cad-suite for Verilator ≥ 5.028
- Docs: `docs/setup.md`, `docs/regmap.md`, this file

## What is next (Phase 2 kickoff)

1. `pktforge_hdr_builder.sv`: reads config from regfile outputs, produces Eth+IPv4+UDP+BTH bytes on AXI-Stream master. Golden-model-is-law: byte-exact diff against `model/golden.py` on cocotb runs
2. IP header checksum computation lives inside hdr_builder (small combinational adder tree)
3. Real-capture ICRC validation is optional and can be revisited when a two-host test setup is available (single-host Soft-RoCE loopback does not produce sniffable traffic; see PR history)

## Known limitations / TODOs

- ICRC only cross-validated against Scapy contrib, not against a real Soft-RoCE capture. Sufficient for now but revisit when two-host capture is possible
- Golden model IPv6 path not implemented (spec Annex A17 masking differs slightly)
- Sweep engine currently supports one dim advancing per packet; multi-dim advance is lockstep (all dims step together each packet). If pktgen semantics is nested (inner dim wraps before outer advances), revisit
- No `pktforge_checker` yet (Phase 4)
- No rate limiter yet (Phase 3)
- `synth/` directory exists but is empty; populated when board bring-up starts
- Local sim requires the repo to live at a space-free path (Verilator + GNU Make cannot handle spaces in source paths). Workaround documented in `docs/setup.md`. CI is unaffected

## Environment notes

- CI runs Python 3.12 on ubuntu-latest
- Scapy tests skip cleanly when Scapy is missing
- Cocotb tests skip cleanly when Cocotb is missing

## Last commit

See `git log -1`.
