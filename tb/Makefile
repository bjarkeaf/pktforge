# cocotb dispatch. Called from the top-level Makefile as:
#   make -C tb MOD=<name>
#
# MOD=all (default) runs every tb/test_*.py via pytest, which in turn invokes
# the cocotb runner for each test module. MOD=<name> runs one suite.
#
# Requires: verilator, cocotb, cocotb-bus, scapy, pyyaml. See docs/setup.md.

PYTHON ?= python3
MOD ?= all

.PHONY: all one

all:
	$(PYTHON) -m pytest . -v

one:
	$(PYTHON) -m pytest test_$(MOD).py -v

# Route "make MOD=x" through the one-suite target when MOD != all.
ifneq ($(MOD),all)
default: one
else
default: all
endif

.DEFAULT_GOAL := default
