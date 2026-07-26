"""End-to-end integration tests for the metaproc-domain metabrowser plugin.

The plugin is `metaproc.metabrowser_plugin` (subpackage of metaproc;
ships in metaproc's wheel). It's auto-discovered via the
metabrowser.plugins entry point declared in metaproc's
pyproject.toml; its sidekick handlers are mounted at
/api/plugin/metaproc/<route>. These tests use the actual plugin
(not a fixture) to confirm the framework sees a real Python
entry-point plugin alongside the built-ins.
"""

from __future__ import annotations

import pytest
from metabrowser.plugin_loader.discovery import discover_plugins
from metabrowser.plugin_loader.static_assets import build_plugin_routes
from starlette.applications import Starlette
from starlette.testclient import TestClient


@pytest.fixture
def metaproc_app() -> TestClient:
    """An app with only the metaproc plugin's routes (filtered from
    discovery). Avoids loading the full metabrowser server, which has
    a lot of unrelated wiring; we just want the plugin-static + data-hook
    routes for assertions.
    """
    discovered = discover_plugins()
    metaproc = next((p for p in discovered.plugins if p.name == "metaproc"), None)
    assert metaproc is not None, (
        "metaproc.metabrowser_plugin entry-point not discovered — is metaproc in the dev env?"
    )
    routes = build_plugin_routes([metaproc])
    app = Starlette(routes=routes)
    return TestClient(app)


def test_info_handler_returns_plugin_metadata(metaproc_app: TestClient) -> None:
    resp = metaproc_app.get("/api/plugin/metaproc/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "metaproc"
    assert "process-spec" in data["kinds"]
    assert "resource-report" in data["kinds"]
    assert "structure-report" in data["kinds"]
    assert "runpool-log" in data["kinds"]
    assert "process-log" in data["kinds"]
    assert "structure-report" in data["routes"]


def test_static_index_js_serves(metaproc_app: TestClient) -> None:
    """The plugin's index.js is reachable via /plugin-static/metaproc/."""
    resp = metaproc_app.get("/plugin-static/metaproc/index.js")
    assert resp.status_code == 200
    assert resp.headers["ETag"]
    # The placeholder comment from the file should be present.
    assert "metabrowser" in resp.text.lower()


def test_static_manifest_toml_serves(metaproc_app: TestClient) -> None:
    """The manifest is also reachable via /plugin-static/ — useful for
    JS code that wants to read the plugin's metadata client-side."""
    resp = metaproc_app.get("/plugin-static/metaproc/manifest.toml")
    assert resp.status_code == 200
    assert "[plugin]" in resp.text
    assert 'name = "metaproc"' in resp.text


def test_unknown_plugin_route_404(metaproc_app: TestClient) -> None:
    resp = metaproc_app.get("/api/plugin/metaproc/no-such-hook")
    assert resp.status_code == 404
