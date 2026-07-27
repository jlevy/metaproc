"""Phase 3h — end-to-end plugin discovery + registration.

Real flow exercised here: the SDK source loads, every built-in plugin's
``index.js`` runs, the metaproc plugin's ``index.js`` runs, and the
resulting view registry covers every (kind, viewId) declared in any
loaded manifest. If a manifest declares a ``[[view]]`` block but no
``registerView`` call lands for it, the plugin's contract is broken
and the shell would paint "Unknown view" for that file.

We use Node's ``vm`` module via subprocess (see ``tests/dom/load_plugins.js``)
instead of jsdom — the SDK only needs ``window`` + a tiny ``document``
stub at registration time, so a real DOM would add 150 MB of test deps
for negligible gain.

The test ALSO validates that --plugins-dir / METABROWSER_PLUGINS_DIRS
plug into the same flow: a fixture plugin under tests/fixtures/sample_plugin/
shows up in the loader's plugin list when passed as an extra dir.
"""

from __future__ import annotations

import importlib.resources
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from metaproc.metabrowser_plugin import plugin_dir
from tests.plugin_manifest_contract import manifest_contracts_json

METABROWSER_PACKAGE_ROOT = Path(str(importlib.resources.files("metabrowser")))
METAPROC_PLUGIN_ROOT = plugin_dir()
LOADER_JS = Path(__file__).resolve().parent / "dom" / "load_metabrowser_plugins.js"
MANIFEST_CONTRACTS = manifest_contracts_json(
    METABROWSER_PACKAGE_ROOT,
    METAPROC_PLUGIN_ROOT,
)
NODE_SUBPROCESS_TIMEOUT_SECONDS = 30


def _has_node() -> bool:
    return shutil.which("node") is not None


@pytest.fixture
def shim_output() -> dict[str, Any]:
    """Run the JS shim against the installed package resources."""
    if not _has_node():
        pytest.skip("node not available; skipping JS-side plugin shim")
    result = subprocess.run(
        [
            "node",
            str(LOADER_JS),
            str(METABROWSER_PACKAGE_ROOT),
            str(METAPROC_PLUGIN_ROOT),
            MANIFEST_CONTRACTS,
        ],
        capture_output=True,
        text=True,
        timeout=NODE_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, (
        f"shim exited {result.returncode}: stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    last_line = result.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def test_shim_loads_every_builtin_plugin(shim_output: dict[str, Any]) -> None:
    """All five built-in plugins (markdown, agent_log, binary, text,
    unknown_jsonl) load + run their index.js."""
    plugins = shim_output["plugins"]
    for expected in ("markdown", "agent_log", "binary", "text", "unknown_jsonl"):
        assert expected in plugins, f"plugin {expected!r} should load; got {plugins!r}"


def test_shim_loads_metaproc_plugin(shim_output: dict[str, Any]) -> None:
    plugins = shim_output["plugins"]
    assert "metaproc" in plugins


def test_shim_loads_metaproc_visual_assets(shim_output: dict[str, Any]) -> None:
    assert shim_output["metaproc_viz_loaded"] is True
    assert shim_output["metaproc_domain_views_loaded"] is True
    assert shim_output["metaproc_domain_view_handlers_loaded"] is True
    assert shim_output["metaproc_visual_has_dispose"] is True
    assert shim_output["metaproc_charts_has_dispose"] is True


def test_shim_reports_no_load_errors(shim_output: dict[str, Any]) -> None:
    """Every plugin's IIFE should evaluate cleanly. A non-empty errors
    list means an index.js threw at module-load time."""
    errs = shim_output["errors"]
    assert errs == [], f"plugin index.js errors at load: {errs!r}"


def test_every_declared_view_has_a_registration(shim_output: dict[str, Any]) -> None:
    """For each [[view]] block in any loaded plugin's manifest, the
    plugin's index.js (or another plugin's) must call registerView for
    the matching (kind, viewId). Missing registrations would show
    'Unknown view' in the actual shell.
    """
    missing = [
        f"({r['kind']}, {r['view']})" for r in shim_output["registrations"] if not r["registered"]
    ]
    assert missing == [], (
        "manifests declare these views but no registerView fired:\n  " + "\n  ".join(missing)
    )


def test_no_render_time_namespace_violations(shim_output: dict[str, Any]) -> None:
    """The render-time-only namespace rule: a plugin's top-level IIFE
    MUST NOT dereference mb.builtins.<other> unless <other> already
    loaded. The shim tracks every load-time read of mb.builtins.<X>;
    a read that resolved to undefined is a foot-gun (the consumer will
    explode the moment it tries to use the value). See
    metabrowser-plugins.md → "Render-time-only namespace rule".
    """
    violations = shim_output.get("namespace_violations", [])
    assert violations == [], (
        "plugins read another plugin's mb.builtins namespace at script-load "
        "time and got undefined; move the access inside a render() callback. "
        f"Violations: {violations!r}"
    )


def test_namespace_rule_is_enforced(tmp_path: Path) -> None:
    """The enforcement actually trips on a violating fixture: a plugin
    that reads mb.builtins.nonexistent at top level should produce a
    namespace_violations entry.
    """
    if not _has_node():
        pytest.skip("node not available")
    bad = tmp_path / "bad_plugin"
    bad.mkdir()
    (bad / "manifest.toml").write_text(
        '[plugin]\nname = "bad_plugin"\n[[kind]]\nid = "bad"\n'
        'match = { ext = ".bad" }\n[[view]]\nkind = "bad"\nid = "v"\nlabel = "V"\n'
    )
    (bad / "index.js").write_text(
        "(function() {\n"
        "  var mb = window.metabrowser;\n"
        "  // Top-level read of an unloaded namespace — the foot-gun.\n"
        "  var probe = mb.builtins.nonexistent;\n"
        "  void probe;\n"
        "  mb.registerView('bad', 'v', { render: function () {} });\n"
        "})();\n"
    )
    result = subprocess.run(
        [
            "node",
            str(LOADER_JS),
            str(METABROWSER_PACKAGE_ROOT),
            str(METAPROC_PLUGIN_ROOT),
            manifest_contracts_json(
                METABROWSER_PACKAGE_ROOT,
                METAPROC_PLUGIN_ROOT,
                extra_dirs=(tmp_path,),
            ),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=NODE_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, f"shim failed: {result.stderr!r}"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    violations = payload.get("namespace_violations", [])
    assert any(v["plugin"] == "bad_plugin" and v["key"] == "nonexistent" for v in violations), (
        f"expected to flag bad_plugin reading mb.builtins.nonexistent; got {violations!r}"
    )


def test_extra_plugins_dir_is_loaded() -> None:
    """A plugin directory passed as an extra arg goes through the same
    discovery + index.js load path. Uses the existing sample_plugin
    fixture which has its own registerView call."""
    if not _has_node():
        pytest.skip("node not available")
    fixture_dir = Path(__file__).resolve().parent / "fixtures"
    if not (fixture_dir / "sample_plugin" / "index.js").is_file():
        pytest.skip("sample_plugin fixture missing")
    result = subprocess.run(
        [
            "node",
            str(LOADER_JS),
            str(METABROWSER_PACKAGE_ROOT),
            str(METAPROC_PLUGIN_ROOT),
            manifest_contracts_json(
                METABROWSER_PACKAGE_ROOT,
                METAPROC_PLUGIN_ROOT,
                extra_dirs=(fixture_dir,),
            ),
            str(fixture_dir),
        ],
        capture_output=True,
        text=True,
        timeout=NODE_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, f"shim failed: {result.stderr!r}"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert "sample_plugin" in payload["plugins"], (
        f"sample_plugin fixture should load via extra-dir; got {payload['plugins']!r}"
    )
