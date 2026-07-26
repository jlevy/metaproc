"""Browser lifecycle contract for asynchronous Metaproc views."""

from __future__ import annotations

import importlib.resources
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from metaproc.metabrowser_plugin import plugin_dir
from tests.plugin_manifest_contract import manifest_contracts_json

SHIM = Path(__file__).resolve().parent / "dom" / "test_metaproc_plugin_lifecycle.js"
METABROWSER_PACKAGE_ROOT = Path(str(importlib.resources.files("metabrowser")))
MANIFEST_CONTRACTS = manifest_contracts_json(
    METABROWSER_PACKAGE_ROOT,
    plugin_dir(),
)
NODE_SUBPROCESS_TIMEOUT_SECONDS = 30


def test_async_views_ignore_stale_responses_and_share_requests_until_dispose() -> None:
    if shutil.which("node") is None:
        pytest.skip("node not available")
    result = subprocess.run(
        [
            "node",
            str(SHIM),
            str(METABROWSER_PACKAGE_ROOT),
            str(plugin_dir()),
            MANIFEST_CONTRACTS,
        ],
        capture_output=True,
        text=True,
        timeout=NODE_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert [call["container"] for call in payload["renderCalls"]] == [
        "second",
        "shared-visual",
        "retry-visual",
    ]
    assert payload["renderCalls"][0]["viz"] == {"name": "second"}
    assert payload["renderCalls"][1]["viz"]["name"] == "shared"
    assert payload["renderCalls"][2]["viz"] == {"name": "recovered"}
    assert payload["firstHtml"].startswith('<div class="loading"')
    assert payload["secondHtml"] == "rendered:second"
    assert payload["sharedVisualHtml"] == "rendered:shared"
    assert "sidekick unavailable" in payload["failedVisualHtml"]
    assert payload["retryVisualHtml"] == "rendered:recovered"
    assert payload["secondChartsHtml"] == "charts:pool"
    assert "No stats data available" in payload["secondStatsHtml"]
    assert payload["freshChartsHtml"] == "charts:fresh-pool"
    assert payload["vizModelRequestCount"] == 1
    assert payload["failedVizModelRequestCount"] == 2
    assert payload["chartRequestCount"] == 2
    assert payload["statsRequestCount"] == 1
    assert payload["chartDisposeCalls"] == 1
    assert payload["chartRenderCalls"] == 2
