.PHONY: test lint golden-check clean help

PYTHON ?= python3
VERILATOR ?= verilator

RTL_DIR := rtl
TB_DIR := tb
MODEL_DIR := model

# Default: all suites. Override with MOD=<name> to run one.
MOD ?= all

help:
	@echo "make test              run cocotb suites (MOD=<name> to filter)"
	@echo "make lint              verilator lint-only on rtl/"
	@echo "make golden-check      Python golden-model self-tests"
	@echo "make clean             remove sim/build artifacts"

test:
	$(MAKE) -C $(TB_DIR) MOD=$(MOD)

lint:
	@files=$$(find $(RTL_DIR) -name '*.sv' 2>/dev/null); \
	if [ -z "$$files" ]; then \
		echo "no rtl yet, skipping lint"; \
	else \
		set -e; \
		for f in $$files; do \
			mod=$$(basename $$f .sv); \
			echo "lint: $$f (--top-module $$mod)"; \
			$(VERILATOR) --lint-only -Wall -I$(RTL_DIR) --top-module $$mod $$f; \
		done; \
	fi

golden-check:
	$(PYTHON) -m pytest $(MODEL_DIR) -v

clean:
	rm -rf $(TB_DIR)/sim_build $(TB_DIR)/results
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete
	find . -name 'obj_dir' -type d -exec rm -rf {} + 2>/dev/null || true
