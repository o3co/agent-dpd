# DPD — convenience targets for development.
# The real install logic lives in install.sh — Makefile is just a familiar
# entry point for users who prefer `make`.

PYTHON ?= python3.11
VENV   ?= mcp/.venv
PY      = $(VENV)/bin/python

.PHONY: help install test clean

help:
	@echo "DPD make targets:"
	@echo "  install   run ./install.sh (venv + editable install + Claude Code MCP register)"
	@echo "  test      run pytest"
	@echo "  clean     remove .venv, caches, build artifacts"
	@echo ""
	@echo "Direct one-liner (no clone needed):"
	@echo "  curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash"

install:
	./install.sh

test:
	$(PY) -m pytest mcp/tests/ -q

clean:
	rm -rf $(VENV) mcp/.pytest_cache mcp/dist mcp/src/*.egg-info
	find mcp -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
