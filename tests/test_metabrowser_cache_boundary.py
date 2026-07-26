"""Cache-boundary contracts for metaproc's MetaBrowser plugin."""

from __future__ import annotations

import json
from pathlib import Path

from metabrowser.charts import _CHARTS_CACHE as _AGENT_CACHE
from metabrowser.charts import extract_agent_charts

from metaproc.metabrowser_plugin.charts import _CHARTS_CACHE as _RUNPOOL_CACHE
from metaproc.metabrowser_plugin.charts import extract_runpool_charts


def test_chart_caches_include_extractor_kind(tmp_path: Path) -> None:
    """Plugin and core chart extractors never share cached payloads."""
    _AGENT_CACHE.clear()
    _RUNPOOL_CACHE.clear()
    log = tmp_path / "ambiguous.jsonl"
    log.write_text(
        json.dumps(
            {
                "event": "pool_start",
                "pool_id": "pool-test",
                "backend": "local",
                "max_concurrency": 2,
                "ts": "2026-04-24T12:00:00+00:00",
            }
        )
        + "\n"
    )

    runpool = extract_runpool_charts(log)
    agent = extract_agent_charts(log)

    assert agent is not runpool
    assert len(_AGENT_CACHE) == 1
    assert len(_RUNPOOL_CACHE) == 1
