# Session status

Handoff document. Update at the end of every session. Read at the start of every session before touching anything.

## Current state

**Phase 0, exit gate cleared.** Repo initialized, Python golden model + FCS + ICRC implemented and cross-validated against Scapy's `contrib.roce` reference. Cocotb harness skeleton in place with a passthrough smoke test. No real RTL yet.

## What is done

- Repo scaffold: LICENSE (Apache-2.0), `.gitignore`, `README.md`, `Makefile`, `.github/workflows/ci.yml`
- Config loader (`model/config.py`) with YAML schema (`model/config_schema.yaml`), tested
- Ethernet FCS helper (`model/fcs.py`), tested
- RoCEv2 ICRC implementation (`model/icrc.py`) with masking helpers and unit tests. Cross-validated byte-exact against `scapy.contrib.roce` over a 200-shape random sweep (`test_against_scapy_reference`). Real-capture validation remains available as opt-in but is not required for Phase 0 exit
- Scapy golden-model frame factory (`model/golden.py`) for RoCEv2 and TCP+HTTP, with sweep-per-packet, PSN/seq incrementing, FCS/ICRC appending
- cocotb harness: `tb/conftest.py`, `tb/lib/axis.py` (AXI-Stream master driver + slave monitor with tkeep/backpressure), `tb/test_passthrough.py` (smoke test)
- Trivial `rtl/pktforge_passthrough.sv` to prove the harness end-to-end (will be deleted when the first real module lands)
- Docs: `docs/setup.md`, this file

## What is next (Phase 1 kickoff)

1. Decide first RTL module. Two reasonable starting points:
   - `pktforge_regfile_axil.sv`: AXI-Lite register file. Small, low-risk, unblocks every other module. Good warm-up
   - `pktforge_hdr_builder.sv`: header assembly pipeline (Eth/IP/UDP/BTH). Higher value, exercises the golden model directly, but touches the sweep engine
2. Write the cocotb test first (golden-model-is-law). Byte-exact diff against `model/golden.py`
3. When Phase 2 opens (first real RTL merged), fix sim CI: build Verilator ≥ 5.028 from source or switch to oss-cad-suite
4. Real-capture ICRC validation is optional and can be revisited when a two-host test setup is available (single-host Soft-RoCE loopback does not produce sniffable traffic; see `docs/status.md` history)

## Known limitations / TODOs

- ICRC only cross-validated against Scapy contrib, not against a real Soft-RoCE capture. Sufficient for now but revisit when two-host capture is possible
- Golden model IPv6 path not implemented (spec Annex A17 masking differs slightly)
- Sweep engine currently supports one dim advancing per packet; multi-dim advance is lockstep (all dims step together each packet). If pktgen semantics is nested (inner dim wraps before outer advances), revisit
- No `pktforge_checker` yet (Phase 4)
- No rate limiter yet (Phase 3)
- `synth/` directory exists but is empty; populated when board bring-up starts
- CI `sim` job is deferred. cocotb 2.0.1 requires Verilator ≥ 5.028 (needs `clearEvalNeeded`, `doInertialPuts`) but Ubuntu 24.04's apt ships 5.020. Phase 2 opens by either (a) building Verilator from source in the workflow (~5 min per run, cache) or (b) using oss-cad-suite. Until then only `golden-check` and `lint` gate merges. Local `make test` still works if you install a recent Verilator per `docs/setup.md`

## Environment notes

- CI runs Python 3.12 on ubuntu-latest
- Scapy tests skip cleanly when Scapy is missing
- Cocotb tests skip cleanly when Cocotb is missing

## Last commit

See `git log -1`.
