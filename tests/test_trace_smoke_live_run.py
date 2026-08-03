"""Opt-in smoke tests for ``metaproc trace`` against a real local run.

Default tests must not depend on mutable run artifacts. Set
``METAPROC_TRACE_SMOKE_RUN_DIR`` to a complete local run directory to exercise
the full extract -> link -> view pipeline against real per-attempt JSONL files,
runpool events, and result state.

The assertions deliberately avoid assumptions about a particular workflow,
provider, item count, or cost.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metaproc.trace.named_views import cost_rows, health_rows
from metaproc.trace.runner import extract_trace

_TRACE_SMOKE_RUN_DIR_ENV = "METAPROC_TRACE_SMOKE_RUN_DIR"
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


def test_extract_against_live_run_yields_core_span_kinds() -> None:
    spans = extract_trace(_trace_smoke_run_dir())
    kinds = {s.kind for s in spans}
    assert {"run", "step", "attempt"}.issubset(kinds)


def test_health_view_accepts_live_run() -> None:
    spans = extract_trace(_trace_smoke_run_dir())
    rows = health_rows(spans)
    assert isinstance(rows, list)


def test_cost_view_accepts_live_run() -> None:
    spans = extract_trace(_trace_smoke_run_dir())
    rows = cost_rows(spans)
    assert isinstance(rows, list)
