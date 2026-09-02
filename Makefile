# Makefile for local and CI development workflows.
# See docs/development.md.

.DEFAULT_GOAL := default
.NOTPARALLEL:

# Use only the checked-in project configuration. Otherwise uv merges user- and
# system-level settings into uv.lock, which can make it fail on another machine.
# Exported so uv subprocesses that this Makefile does not invoke directly still
# resolve against it; $(UV)/$(UVX) below also pass it explicitly.
UV_CONFIG_FILE := $(CURDIR)/uv.toml
export UV_CONFIG_FILE

# Safe default for every dependency resolution invoked through this Makefile.
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

.PHONY: default install hooks-install format format-markdown lint lint-check test audit lock upgrade build verify clean safeproc-install safeproc-format safeproc-lint-check safeproc-test safeproc-build verify-safeproc

default: install format lint test

install:
	$(UV) sync --all-extras --all-groups --all-packages --locked
	npm ci

hooks-install: install
	npm run prepare

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
	$(UV_RUN) python -m devtools.check_shipped_links
	$(UV_RUN) python -m devtools.check_doc_dates
	$(UV_RUN) python -m devtools.public_hygiene
	$(UV_RUN) python -m devtools.check_supply_chain
	npm run check:browser

lint-check:
	$(UV_RUN) python -m devtools.lint --check
	$(UV_RUN) python -m devtools.check_links
	$(UV_RUN) python -m devtools.check_shipped_links
	$(UV_RUN) python -m devtools.check_doc_dates
	$(UV_RUN) python -m devtools.public_hygiene
	$(UV_RUN) python -m devtools.check_supply_chain
	npm run check:browser
	$(FLOWMARK) --auto --check .

# Shard across the host's cores. The suite is dominated by processes waiting
# on each other rather than by CPU, so workers overlap almost perfectly.
# Override with `make test PYTEST_ARGS=-n0` to debug a worker-ordering issue.
PYTEST_ARGS ?= -n logical

test:
	$(UV_RUN) pytest $(PYTEST_ARGS)

# No advisory waivers are active. SUPPLY-CHAIN-SECURITY.md, "Audited Advisory
# Waivers", owns the rationale and removal condition for any that are added here.
AUDIT_IGNORES ?=

audit:
	npm audit --audit-level=moderate
	$(UV) --preview-features audit-command audit --frozen $(AUDIT_IGNORES)

lock:
	$(UV) lock

upgrade:
	$(UV) lock --upgrade
	$(UV) sync --all-extras --all-groups --all-packages --frozen

build:
	$(UV) build --clear --no-build-isolation
	$(UV_RUN) python -m devtools.check_distribution

verify: install lint-check test audit build verify-safeproc

# ---- safeproc workspace member ----
#
# Targeted gates that pass the nested pyproject.toml explicitly so the package keeps an
# extractable configuration. `verify` includes them; unrelated edit loops may skip them.

SAFEPROC := packages/safeproc
SAFEPROC_CFG := $(SAFEPROC)/pyproject.toml
SAFEPROC_PATHS := $(SAFEPROC)/src $(SAFEPROC)/tests

# Installs only the member and its dev group, without pruning the root environment, so
# a macOS runner needs neither Node nor the Metaproc extras.
safeproc-install:
	$(UV) sync --package safeproc --group dev --locked --inexact

safeproc-format: | safeproc-install
	$(UV_RUN) codespell --write-changes --toml $(SAFEPROC_CFG) $(SAFEPROC_PATHS) $(SAFEPROC)/README.md
	$(UV_RUN) ruff check --fix --config $(SAFEPROC_CFG) $(SAFEPROC_PATHS)
	$(UV_RUN) ruff format --config $(SAFEPROC_CFG) $(SAFEPROC_PATHS)

safeproc-lint-check: | safeproc-install
	$(UV_RUN) codespell --toml $(SAFEPROC_CFG) $(SAFEPROC_PATHS) $(SAFEPROC)/README.md
	$(UV_RUN) ruff check --config $(SAFEPROC_CFG) $(SAFEPROC_PATHS)
	$(UV_RUN) ruff format --check --config $(SAFEPROC_CFG) $(SAFEPROC_PATHS)
	$(UV_RUN) basedpyright --project $(SAFEPROC_CFG)
	$(UV_RUN) python -m devtools.check_safeproc_boundary

safeproc-test: | safeproc-install
	$(UV_RUN) pytest -c $(SAFEPROC_CFG) --rootdir $(SAFEPROC) $(SAFEPROC)/tests

safeproc-build: | safeproc-install
	$(UV) build --package safeproc --no-sources --clear --no-build-isolation -o $(SAFEPROC)/dist
	$(UV_RUN) python -m devtools.check_safeproc_distribution

verify-safeproc: safeproc-lint-check safeproc-test safeproc-build

clean:
	-rm -rf dist/
	-rm -rf packages/safeproc/dist/
	-rm -rf *.egg-info/
	-rm -rf .pytest_cache/
	-rm -rf .ruff_cache/
	-rm -rf .mypy_cache/
	-rm -rf .venv/
	-rm -rf node_modules/
	-find . -type d -name "__pycache__" -exec rm -rf {} +
