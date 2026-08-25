# Session status

Handoff document. Update at the end of every session. Read at the start of every session before touching anything.

## Current state

**Phase 0, mid-scaffold.** Repo initialized, Python golden model + FCS + ICRC implemented, cocotb harness skeleton in place with a passthrough smoke test. No real RTL yet.

## What is done

- Repo scaffold: LICENSE (Apache-2.0), `.gitignore`, `README.md`, `Makefile`, `.github/workflows/ci.yml`
- Config loader (`model/config.py`) with YAML schema (`model/config_schema.yaml`), tested
- Ethernet FCS helper (`model/fcs.py`), tested
- RoCEv2 ICRC implementation (`model/icrc.py`) with masking helpers and unit tests. **NOT YET VALIDATED against real captures**
- Scapy golden-model frame factory (`model/golden.py`) for RoCEv2 and TCP+HTTP, with sweep-per-packet, PSN/seq incrementing, FCS/ICRC appending
- cocotb harness: `tb/conftest.py`, `tb/lib/axis.py` (AXI-Stream master driver + slave monitor with tkeep/backpressure), `tb/test_passthrough.py` (smoke test)
- Trivial `rtl/pktforge_passthrough.sv` to prove the harness end-to-end (will be deleted when the first real module lands)
- Docs: `docs/setup.md`, this file

## What is next (Phase 0 exit)

1. Install tools per `docs/setup.md` (python-scapy/verilator/gtkwave, plus a vendor toolchain when board work starts)
2. Capture Soft-RoCE reference frames per `docs/setup.md` and drop them in `model/ref_pcaps/`
3. Run `pytest model/test_icrc.py::test_against_captures`. If it fails, debug the ICRC masking or byte order in `model/icrc.py` (candidate issues: BTH.Resv8a byte position, ICRC byte endianness on the wire, whether UDP checksum masking is actually zero-fill in real captures). When it passes: update `model/icrc.py` header to remove the UNVALIDATED marker, record here that validation is complete

## Known limitations / TODOs

- ICRC unvalidated (see above)
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
