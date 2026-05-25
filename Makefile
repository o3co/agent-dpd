# DPD — convenience targets for development.
# The real install logic lives in install.sh — Makefile is just a familiar
# entry point for users who prefer `make`.

PYTHON ?= python3.11
VENV   ?= core/server/.venv
PY      = $(VENV)/bin/python

.PHONY: help install test test-unit test-hooks clean

help:
	@echo "DPD make targets:"
	@echo "  install     run ./install.sh (venv + editable install + Cursor symlink/mcp.json patch)"
	@echo "  test        run all tests (unit + hooks)"
	@echo "  test-unit   run pytest only"
	@echo "  test-hooks  run packaging/claude-code/hooks/tests/*.sh only"
	@echo "  clean       remove .venv, caches, build artifacts"
	@echo ""
	@echo "Direct one-liner (no clone needed):"
	@echo "  curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash"

install:
	./install.sh

test: test-unit test-hooks

test-unit:
	$(PY) -m pytest core/server/tests/ -q

test-hooks:
	@for t in packaging/claude-code/hooks/tests/*.sh; do \
	  echo "==> running $$t"; \
	  bash "$$t" || exit 1; \
	done

clean:
	rm -rf $(VENV) core/server/.pytest_cache core/server/dist core/server/src/*.egg-info
	find core/server -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
