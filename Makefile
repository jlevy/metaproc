# Makefile for local and CI development workflows.
# See docs/development.md.

.DEFAULT_GOAL := default
.NOTPARALLEL:

UV_EXCLUDE_NEWER ?= 14 days
export UV_EXCLUDE_NEWER
UV := uv --config-file $(CURDIR)/uv.toml
UVX := uvx --config-file $(CURDIR)/uv.toml
UV_RUN := $(UV) run --frozen
FLOWMARK_VERSION := 0.3.2
FLOWMARK_EXCEPTION := 2026-07-16T00:00:00Z
FLOWMARK := $(UVX) --exclude-newer-package 'flowmark-rs=$(FLOWMARK_EXCEPTION)' flowmark-rs@$(FLOWMARK_VERSION)

unexport NPM_CONFIG_FROZEN_LOCKFILE
unexport NPM_CONFIG_MINIMUM_RELEASE_AGE
unexport NPM_CONFIG_BEFORE

.PHONY: default install hooks-install format format-markdown lint lint-check test audit lock upgrade build verify clean

default: install format lint test

install:
	$(UV) sync --all-extras --all-groups --locked
	npm ci

hooks-install: install
	npx --no-install lefthook install

format lint lint-check test audit build: | install

format:
	$(MAKE) format-markdown
	$(UV_RUN) ruff format src tests devtools
	npm run format:browser

format-markdown:
	$(FLOWMARK) --auto --inplace --nobackup .

lint:
	$(UV_RUN) python -m devtools.lint
	$(UV_RUN) python -m devtools.check_links
	$(UV_RUN) python -m devtools.public_hygiene
	$(UV_RUN) python -m devtools.check_supply_chain
	npm run check:browser

lint-check:
	$(UV_RUN) python -m devtools.lint --check
	$(UV_RUN) python -m devtools.check_links
	$(UV_RUN) python -m devtools.public_hygiene
	$(UV_RUN) python -m devtools.check_supply_chain
	npm run check:browser
	$(FLOWMARK) --auto --check .

test:
	$(UV_RUN) pytest

audit:
	npm audit --audit-level=moderate
	$(UV) --preview-features audit-command audit --frozen

lock:
	$(UV) lock

upgrade:
	$(UV) lock --upgrade
	$(UV) sync --all-extras --all-groups --frozen

build:
	$(UV) build --clear --no-build-isolation
	$(UV_RUN) python -m devtools.check_distribution

verify: install lint-check test audit build

clean:
	-rm -rf dist/
	-rm -rf *.egg-info/
	-rm -rf .pytest_cache/
	-rm -rf .ruff_cache/
	-rm -rf .mypy_cache/
	-rm -rf .venv/
	-rm -rf node_modules/
	-find . -type d -name "__pycache__" -exec rm -rf {} +
