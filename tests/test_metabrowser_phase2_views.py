"""Phase 2 browser surface contracts (spec §§8.1–8.5, epic internal-reference)."""

from __future__ import annotations

from pathlib import Path

from metabrowser.server import VIEW_REGISTRY, _classify_with_plugins, _views_for_kind

from metaproc.metabrowser_plugin import plugin_dir


def test_resource_report_kind_exposes_resources_treemap_and_raw() -> None:
    """P2-2: treemap lives alongside resources + raw on the resource-report kind.

    Source of truth is the metaproc plugin manifest now; _views_for_kind
    surfaces it the same way for the tab strip.
    """
    views = _views_for_kind("resource-report")
    ids = [v["id"] for v in views]
    assert "resources" in ids
    assert "treemap" in ids
    assert "raw" in ids
    default_ids = [v["id"] for v in views if v.get("default")]
    assert default_ids == ["resources"]


def test_resources_json_classification_drives_phase2_views(tmp_path: Path) -> None:
    fixture = tmp_path / "resources.json"
    fixture.write_text('{"schema":"metaproc.resources/v1","run_id":"r1"}')
    kind = _classify_with_plugins(fixture, ".json")
    assert kind == "resource-report"
    views = _views_for_kind(kind)
    for required in ("resources", "treemap", "raw"):
        assert any(v["id"] == required for v in views)


def test_view_registry_ids_remain_unique_per_kind() -> None:
    """Defensive guard: duplicate view ids would clobber tab selection.
    Covers built-in VIEW_REGISTRY only; plugin manifest validation enforces
    this for plugin-supplied views via PluginManifest.validate_consistency.
    """
    for kind, views in VIEW_REGISTRY.items():
        ids = [v["id"] for v in views]
        assert len(ids) == len(set(ids)), f"duplicate view ids in {kind}: {ids}"


def test_views_for_kind_defaults_to_first_view_when_none_declared() -> None:
    """A kind with tabbed views must never render with every tab hidden."""
    views = _views_for_kind("unknown-jsonl")
    assert views[0]["id"] == "log"
    assert views[0]["default"] is True
    assert [v["id"] for v in views if v.get("default")] == ["log"]


def test_resource_report_view_seeds_visual_decorator_state() -> None:
    domain_views_js = (plugin_dir() / "domain_views.js").read_text()
    assert "currentVisualResources = payload" in domain_views_js
    assert "decorators.push(makeResourceDecorator" in domain_views_js
    assert "decorators: decorators" in domain_views_js
