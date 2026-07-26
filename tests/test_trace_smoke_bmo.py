"""Opt-in smoke test: ``metaproc trace`` against a historical BMO Tier 1 run.

Default tests must not depend on mutable ``runs/local`` artifacts. Set
``METAPROC_TRACE_SMOKE_BMO_TIER1_RUN_DIR`` to the complete local cohort
directory when you want to exercise the full extract -> link -> view pipeline
against real per-attempt JSONL files, arena logs, web bundles, runpool events,
and result.yaml state. Confirms:

- expected V1 span kinds are emitted at the live-cohort scale;
- ``--health`` surfaces the Perplexity ``silent_failure`` cluster
  in one query (the audit-replication acceptance);
- ``--cost`` reconciles bundle + agent costs into a single rollup
  (the $50-vs-$780 visibility gap from 2026-05-10).

Plan reference: § Phase 1 / E.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metaproc.trace.named_views import cost_rows, health_rows
from metaproc.trace.runner import extract_trace

_TRACE_SMOKE_RUN_DIR_ENV = "METAPROC_TRACE_SMOKE_BMO_TIER1_RUN_DIR"
_TRACE_SMOKE_RUN_DIR_VALUE = os.environ.get(_TRACE_SMOKE_RUN_DIR_ENV)
_TRACE_SMOKE_RUN_DIR = (
    Path(_TRACE_SMOKE_RUN_DIR_VALUE).expanduser().resolve() if _TRACE_SMOKE_RUN_DIR_VALUE else None
)


pytestmark = pytest.mark.skipif(
    _TRACE_SMOKE_RUN_DIR is None,
    reason=f"set {_TRACE_SMOKE_RUN_DIR_ENV} to run live trace smoke validation",
)


def _trace_smoke_run_dir() -> Path:
    assert _TRACE_SMOKE_RUN_DIR is not None
    if not _TRACE_SMOKE_RUN_DIR.is_dir():
        pytest.skip(f"{_TRACE_SMOKE_RUN_DIR_ENV} does not exist: {_TRACE_SMOKE_RUN_DIR}")
    if not (_TRACE_SMOKE_RUN_DIR / ".logs" / "tasks").is_dir():
        pytest.skip(
            f"{_TRACE_SMOKE_RUN_DIR_ENV} is missing per-attempt logs: "
            f"{_TRACE_SMOKE_RUN_DIR / '.logs' / 'tasks'}"
        )
    return _TRACE_SMOKE_RUN_DIR


def test_extract_against_live_bmo_tier1_cohort_yields_expected_kinds():
    spans = extract_trace(_trace_smoke_run_dir())
    kinds = {s.kind for s in spans}
    # All V1 span kinds we promised to emit
    assert {
        "run",
        "level",
        "step",
        "item",
        "attempt",
        "agent_session",
        "tool_call",
        "subprocess",
        "provider_call",
        "internal",
    }.issubset(kinds)
    # Tier 1 = 9 tickers x 6 agent steps = 54 attempts
    attempts = [s for s in spans if s.kind == "attempt"]
    assert len(attempts) == 54


def test_health_view_surfaces_perplexity_silent_failure_cluster():
    """The hand-derived audit took ad-hoc Python to surface; the trace
    CLI should produce the same finding in one query.
    """
    spans = extract_trace(_trace_smoke_run_dir())
    rows = health_rows(spans)
    web_silent = [
        r for r in rows if r["source"] == "web-bundle" and r["status"] == "silent_failure"
    ]
    assert len(web_silent) == 1
    # One silent_failure aggregate per ticker (9 tickers in Tier 1)
    assert web_silent[0]["count"] == 9


def test_cost_view_includes_both_bundle_and_agent_sources():
    """Closes the $50-vs-$780 visibility gap: bundle and agent costs must
    both appear in one rollup so the operator can't miss the agent
    portion.
    """
    spans = extract_trace(_trace_smoke_run_dir())
    rows = cost_rows(spans)
    sources = {r["source"] for r in rows}
    assert "web-bundle" in sources
    assert "claude-agent" in sources
    total = sum(float(r["cost"]) for r in rows)
    # Sanity: a 9-ticker tech-SaaS tier should be at least $50 total
    # (bundles + agent sessions). Loose bound; the live cohort came in
    # around $70.
    assert total > 50.0
