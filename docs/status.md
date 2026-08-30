# Session status

Handoff document. Update at the end of every session. Read at the start of every session before touching anything.

## Current state

**Phase 3 in progress.** Phase 0/1/2 exit gates cleared. Full datapath is wired: `pktforge_top` composes regfile + hdr_builder + ICRC appender + FCS appender behind a single AXI-Lite subordinate and a single AXI-Stream master. End-to-end cocotb test writes config over AXI-Lite, pulses CTRL.START, and diffs captured frames byte-exact against `model/golden.py` with append_icrc=True and append_fcs=True. Header builder is now trailer-aware (adjusts payload length + IP.len/UDP.len based on append_icrc_i/append_fcs_i).

## What is done

- Repo scaffold: LICENSE (Apache-2.0), `.gitignore`, `README.md`, `Makefile`, `.github/workflows/ci.yml`
- Config loader (`model/config.py`) with YAML schema (`model/config_schema.yaml`), tested
- Ethernet FCS helper (`model/fcs.py`), tested
- RoCEv2 ICRC implementation (`model/icrc.py`) with masking helpers and unit tests. Cross-validated byte-exact against `scapy.contrib.roce` over a 200-shape random sweep (`test_against_scapy_reference`). Real-capture validation remains available as opt-in but is not required for Phase 0 exit
- Scapy golden-model frame factory (`model/golden.py`) for RoCEv2 and TCP+HTTP, with sweep-per-packet, PSN/seq incrementing, FCS/ICRC appending
- cocotb harness: `tb/conftest.py`, `tb/lib/axis.py` (AXI-Stream master driver + slave monitor with tkeep/backpressure), `tb/lib/axil.py` (AXI-Lite master BFM)
- `rtl/pktforge_regfile_axil.sv`: AXI-Lite register file per `docs/regmap.md`. 32-bit data, 8-bit address, per-byte wstrb, RW1S CTRL pulses, RO ID/VERSION/STATUS/PACKETS_SENT, SLVERR on reserved/unaligned. Fully tested by `tb/test_regfile.py`
- `rtl/pktforge_hdr_builder.sv`: RoCEv2 header/payload generator. Consumes regfile output bus; emits one Eth+IPv4+UDP+BTH+payload frame per `pkt_valid_i` pulse on AXI-Stream master (DATA_W=32). Internal 24-bit PSN counter loads from `roce_psn_ack_i` on `start_i`. Combinational IP header checksum. Tested by `tb/test_hdr_builder.py` (byte-exact diff against golden across baseline/large/unaligned/DSCP-TTL/ack_req/randomized/backpressure scenarios)
- `rtl/pktforge_sweep.sv`: parametric per-dimension sweep counter. `start_i` loads counter to `min_i`, `advance_i` steps by `step_i` and wraps back to `min_i` when the next value would exceed `max_i`. `step_i==0` disables the sweep (`value_o == base_i`). Tested by `tb/test_sweep.py` against a Python mirror of `_sweep_value`. Instantiated 5 times inside `pktforge_hdr_builder` for the 5 sweep dimensions
- `rtl/pktforge_fcs_appender.sv`: AXI-Stream in → out. Computes CRC-32 (zlib.crc32-compatible) over each frame and appends the 4-byte FCS little-endian, merging into the last input beat's empty lanes when possible. Tested by `tb/test_fcs_appender.py` against `model/fcs.py:compute_fcs`
- `rtl/pktforge_icrc_appender.sv`: RoCEv2 Invariant CRC appender. Same skeleton as fcs_appender, with per-byte masking (offsets 15, 22, 24-25, 40-41, 46 → 0xFF), Ethernet header excluded from the CRC pseudo-packet, and a precomputed initial CRC state (0xDEBB20E3, the state after 8 bytes of 0xFF LRH prefix). Tested by `tb/test_icrc_appender.py` against `model/icrc.py:compute_icrc_ipv4`
- `rtl/pktforge_top.sv`: top-level wrapper. Instantiates regfile, hdr_builder, ICRC appender, and FCS appender in series. Packet controller latches PACKET_COUNT on CTRL.START, drives hdr_builder's trigger with backpressure-safe handshake, and counts PACKETS_SENT. STATUS.DONE asserts when the count is reached or CTRL.STOP fires. `output_opts[1:0]` bits drive both the trailer-aware sizing in hdr_builder and the runtime enable of the ICRC/FCS appender datapaths (each appender becomes a pure pass-through when its enable bit is low). Tested end-to-end by `tb/test_top.py` across all four (icrc,fcs) combinations
- `model/golden.py`: RoCEv2 UDP checksum forced to 0 to match spec-compliant hardware behavior (IB Annex A17.4.5.3)
- CI: sim job enabled, pulls oss-cad-suite for Verilator ≥ 5.028
- Docs: `docs/setup.md`, `docs/regmap.md`, this file

## What is next

1. Rate limiter: pace triggers according to RATE_MODE / RATE_LINE_PERCENT / RATE_IFG_BYTES
2. Board bring-up (Phase 6 in the plan): pin out `pktforge_top` on the DE10-Nano and route to the fabric loopback tap
3. Real-capture ICRC validation remains optional and can be revisited when a two-host test setup is available

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
