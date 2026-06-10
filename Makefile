# Oteru — alvos de desenvolvimento do monorepo.
#
# Requer GNU make + bash. No Windows, use o Git Bash (o make do Git for
# Windows / MSYS2 / Chocolatey); PowerShell puro não funciona.
#
# `make` (sem argumentos) lista os alvos.

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

.DEFAULT_GOAL := help

.PHONY: help setup test lint format dry-run pii-guard up down demo clean

help: ## lista os alvos disponíveis
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## cria o venv, instala o emitter (editable + dev) e ativa o hook de PII
	python -m venv $(VENV)
	$(VENV)/$(BINDIR)/python -m pip install --upgrade pip
	$(VENV)/$(BINDIR)/python -m pip install -e "./$(EMITTER)[dev]"
	git config core.hooksPath .githooks
	@echo "setup OK — hook de pre-commit (PII guard) ativado."

test: ## roda a suíte pytest do emitter
	cd $(EMITTER) && $(VENV_PY) -m pytest

lint: ## ruff check + verificação de formatação
	cd $(EMITTER) && $(VENV_PY) -m ruff check oteru_emitter tests
	cd $(EMITTER) && $(VENV_PY) -m ruff format --check oteru_emitter tests

format: ## aplica ruff format + fixes automáticos
	cd $(EMITTER) && $(VENV_PY) -m ruff check --fix oteru_emitter tests
	cd $(EMITTER) && $(VENV_PY) -m ruff format oteru_emitter tests

dry-run: ## valida o sample sem rede (smoke test)
	cd $(EMITTER) && $(VENV_PY) -m oteru_emitter.cli replay $(SAMPLE) --dry-run

pii-guard: ## escaneia capturas commitadas por PII (python do sistema)
	python scripts/check_pii.py

up: ## sobe o collector (docker compose, detached)
	cd $(COLLECTOR) && docker compose up -d

down: ## derruba o collector
	cd $(COLLECTOR) && docker compose down

demo: up ## sobe o collector, envia 5 batches HTTP e mostra os logs
	cd $(EMITTER) && $(VENV_PY) -m oteru_emitter.cli replay $(SAMPLE) \
		--transport http --limit 5 --max-gap 1
	cd $(COLLECTOR) && docker compose logs --tail 50

clean: ## remove caches de build/teste (preserva o .venv)
	find . -type d -name __pycache__ -not -path '*/.venv/*' -prune -exec rm -rf {} +
	rm -rf $(EMITTER)/*.egg-info $(EMITTER)/.pytest_cache $(EMITTER)/.ruff_cache
