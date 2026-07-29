# Oteru — development targets for the monorepo.
#
# Requires GNU make + bash. On Windows, use Git Bash (the make from Git for
# Windows / MSYS2 / Chocolatey); plain PowerShell will not work.
#
# `make` (no arguments) lists the targets.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

EMITTER   := oteru-emitter
COLLECTOR := oteru-collector
VENV      := $(EMITTER)/.venv
SAMPLE    := samples/telemetry-sample.json

ifeq ($(OS),Windows_NT)
BINDIR := Scripts
else
BINDIR := bin
endif

VENV_PY := .venv/$(BINDIR)/python

# Python for bootstrapping (creating the venv, system-python targets). Prefer
# python3 — macOS/Linux have no bare `python`; fall back to `python` on Windows
# / Git Bash. Override for a specific interpreter: `make setup PYTHON=python3.13`.
PYTHON ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null)

.DEFAULT_GOAL := help

.PHONY: help setup test lint format dry-run pii-guard e2e-signals up up-clickstack up-clickhouse down down-clickhouse demo clean

help: ## list the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## create the venv, install the emitter (editable + dev) and enable the PII hook
	$(PYTHON) -m venv $(VENV)
	$(VENV)/$(BINDIR)/python -m pip install --upgrade pip
	$(VENV)/$(BINDIR)/python -m pip install -e "./$(EMITTER)[dev]"
	git config core.hooksPath .githooks
	@echo "setup OK — pre-commit hook (PII guard) enabled."

test: ## run the emitter's pytest suite
	cd $(EMITTER) && $(VENV_PY) -m pytest

lint: ## ruff check + formatting check
	cd $(EMITTER) && $(VENV_PY) -m ruff check oteru_emitter tests
	cd $(EMITTER) && $(VENV_PY) -m ruff format --check oteru_emitter tests

format: ## apply ruff format + autofixes
	cd $(EMITTER) && $(VENV_PY) -m ruff check --fix oteru_emitter tests
	cd $(EMITTER) && $(VENV_PY) -m ruff format oteru_emitter tests

dry-run: ## validate the sample without network (smoke test)
	cd $(EMITTER) && $(VENV_PY) -m oteru_emitter.cli replay $(SAMPLE) --dry-run

pii-guard: ## scan committed captures for PII (system python)
	$(PYTHON) scripts/check_pii.py

e2e-signals: ## verify every --emit combination lands in ClickHouse (needs `make up-clickhouse`)
	bash scripts/check_signals_e2e.sh

up: ## start the collector (docker compose, detached)
	cd $(COLLECTOR) && docker compose up -d

up-clickstack: ## start the collector forwarding to ClickStack (needs CLICKSTACK_ENDPOINT + CLICKSTACK_API_KEY)
	@cd $(COLLECTOR) && \
	if [ ! -f .env ] && { [ -z "$${CLICKSTACK_ENDPOINT:-}" ] || [ -z "$${CLICKSTACK_API_KEY:-}" ]; }; then \
		echo "error: set CLICKSTACK_ENDPOINT and CLICKSTACK_API_KEY (env vars or $(COLLECTOR)/.env —" \
		     "see $(COLLECTOR)/.env.example). Never commit real values."; \
		exit 1; \
	fi && \
	docker compose -f docker-compose.yml -f docker-compose.clickstack.yml up -d

up-clickhouse: ## start the collector + a self-contained ClickHouse backend (native clickhouse exporter)
	cd $(COLLECTOR) && docker compose -f docker-compose.yml -f docker-compose.clickhouse.yml up -d

down: ## stop the collector
	cd $(COLLECTOR) && docker compose down

down-clickhouse: ## stop the collector + ClickHouse and remove the ClickHouse volume
	cd $(COLLECTOR) && docker compose -f docker-compose.yml -f docker-compose.clickhouse.yml down -v

demo: up ## start the collector, send 5 batches over HTTP and show the logs
	cd $(EMITTER) && $(VENV_PY) -m oteru_emitter.cli replay $(SAMPLE) \
		--transport http --limit 5 --max-gap 1
	cd $(COLLECTOR) && docker compose logs --tail 50

clean: ## remove build/test caches (keeps the .venv)
	find . -type d -name __pycache__ -not -path '*/.venv/*' -prune -exec rm -rf {} +
	rm -rf $(EMITTER)/*.egg-info $(EMITTER)/.pytest_cache $(EMITTER)/.ruff_cache
