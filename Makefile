# DPD — convenience targets for install / test / dev.
# Override PYTHON / VENV at the command line if needed:
#   make install PYTHON=python3.12 VENV=.venv

PYTHON ?= python3.11
VENV   ?= mcp/.venv
PIP     = $(VENV)/bin/pip
PY      = $(VENV)/bin/python
SERVER  = $(abspath $(VENV))/bin/dpd-mcp-server

.PHONY: help install test register dev clean

help:
	@echo "DPD make targets:"
	@echo "  install   create venv at $(VENV) and install editable package + dev deps"
	@echo "  test      run pytest"
	@echo "  register  register dpd-mcp-server with Claude Code (claude mcp add)"
	@echo "  dev       install + register"
	@echo "  clean     remove .venv, caches, build artifacts"
	@echo ""
	@echo "Override PYTHON (default: $(PYTHON)) or VENV (default: $(VENV))."

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)

install: $(VENV)/bin/python
	$(PIP) install --upgrade pip
	$(PIP) install -e 'mcp[dev]'

test: install
	$(PY) -m pytest mcp/tests/ -q

register: install
	@echo "Registering dpd-mcp-server with Claude Code…"
	@claude mcp remove dpd-mcp-server 2>/dev/null || true
	claude mcp add dpd-mcp-server -- $(SERVER)
	@echo ""
	@echo "Restart Claude Code so the mcp__dpd-mcp-server__* tools become discoverable."

dev: install register

clean:
	rm -rf $(VENV) mcp/.pytest_cache mcp/dist mcp/src/*.egg-info
	find mcp -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
