"""Tests for the file-kind detector registry in proc_browser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from metabrowser import server as pb
from metabrowser.server import (
    FILE_KIND_DETECTORS,
    FileContext,
    _classify_with_plugins,
    _views_for_kind,
    classify_file_kind,
    register_file_kind_detector,
)

from metaproc.metabrowser_plugin import sidekick


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_process_spec_detected_via_process_envelope(tmp_path: Path) -> None:
    """Resolved through the metaproc plugin's manifest classifier (priority 100)."""

    fixture = _write(
        tmp_path,
        "process.md",
        "---\nprocess:\n  name: p\n---\n\nbody\n",
    )
    assert _classify_with_plugins(fixture, ".md") == "process-spec"


def test_process_spec_detected_via_schema_token(tmp_path: Path) -> None:

    fixture = _write(
        tmp_path,
        "spec.md",
        "---\nschema: metaproc:ProcessSpec/0.1\nname: p\n---\n",
    )
    assert _classify_with_plugins(fixture, ".md") == "process-spec"


def test_plain_markdown_not_classified_as_process_spec(tmp_path: Path) -> None:
    fixture = _write(tmp_path, "readme.md", "# Just a README\n")
    assert classify_file_kind(fixture, ".md") == "markdown"


def test_markdown_with_unrelated_frontmatter_stays_markdown(tmp_path: Path) -> None:

    fixture = _write(
        tmp_path,
        "note.md",
        "---\ntitle: Note\n---\n\n# Note\n",
    )
    assert _classify_with_plugins(fixture, ".md") == "markdown"


def test_jsonl_classification_via_classifier(tmp_path: Path) -> None:
    """metaproc plugin claims runpool/process at priority 100; legacy chain
    handles agent CLI adapters."""

    fixture = _write(tmp_path, "agent.jsonl", "{}\n")
    assert _classify_with_plugins(fixture, ".jsonl", adapter="claude") == "agent-log"
    assert _classify_with_plugins(fixture, ".jsonl", adapter="runpool") == "runpool-log"
    assert _classify_with_plugins(fixture, ".jsonl", adapter=None) == "unknown-jsonl"


def test_process_spec_views_have_visual_first() -> None:
    """Plugin manifest is the source of truth; first entry is the default Visual."""

    views = _views_for_kind("process-spec")
    assert views[0]["id"] == "visual"
    assert views[0].get("default") is True
    assert "rendered" in {v["id"] for v in views}
    assert "source" in {v["id"] for v in views}


def test_structure_report_classified_and_views_registered(tmp_path: Path) -> None:

    fixture = _write(
        tmp_path,
        "structure-report.md",
        "---\nstructure_report:\n  schema: metaproc:StructureReport/v1\n---\n\n# Report\n",
    )

    assert _classify_with_plugins(fixture, ".md") == "structure-report"
    views = _views_for_kind("structure-report")
    assert views[0]["id"] == "summary"
    assert views[0].get("default") is True
    assert {"artifacts", "graph", "source"}.issubset({view["id"] for view in views})


def test_register_file_kind_detector_extends_registry(tmp_path: Path) -> None:
    fixture = _write(tmp_path, "custom.md", "---\ncustom-kind: x\n---\n")

    def _detect_custom(ctx: FileContext) -> str | None:
        fm = ctx.frontmatter
        if fm and "custom-kind" in fm:
            return "custom"
        return None

    original = list(FILE_KIND_DETECTORS)
    try:
        register_file_kind_detector(_detect_custom)
        # The new detector runs after the built-ins; for an otherwise-plain
        # markdown file we need it to win, so insert instead.
        FILE_KIND_DETECTORS.remove(_detect_custom)
        FILE_KIND_DETECTORS.insert(0, _detect_custom)
        assert classify_file_kind(fixture, ".md") == "custom"
    finally:
        FILE_KIND_DETECTORS[:] = original


class _FakeRequest:
    """Duck-typed stand-in for starlette.Request used by api_viz."""

    def __init__(self, params: dict[str, str]) -> None:
        self.query_params = params


def _run_api_viz(pb, params: dict[str, str]):
    """Drive the metaproc plugin's viz-model handler via its sidekick.

    `pb` is the metabrowser.server module; we set ROOT_DIR through it
    (the sidekick uses metabrowser.paths_safe._safe_path which honors
    ROOT_DIR), then call the handler directly.
    """

    return sidekick.viz_model_handler(cast(Any, _FakeRequest(params)))


def test_api_viz_returns_projected_model(tmp_path: Path, monkeypatch) -> None:

    _write(
        tmp_path,
        "test.process.md",
        "---\nprocess:\n  name: p\n  description: d\n---\n\nbody\n",
    )
    monkeypatch.setattr(pb, "ROOT_DIR", tmp_path)

    resp = _run_api_viz(pb, {"process": "test.process.md"})
    payload = json.loads(bytes(resp.body))
    viz = payload["viz"]
    assert viz["schema"] == "metaproc:VizModel/0.2"
    assert viz["header"]["name"] == "p"
    assert viz["header"]["description"] == "d"
    assert viz["root_process"].endswith(".process.md")


def test_api_viz_returns_404_for_missing_process(tmp_path: Path, monkeypatch) -> None:

    monkeypatch.setattr(pb, "ROOT_DIR", tmp_path)

    resp = _run_api_viz(pb, {"process": "missing.md"})
    assert resp.status_code == 404


def test_api_viz_returns_400_for_non_process_md_suffix(tmp_path: Path, monkeypatch) -> None:
    """A file with a valid ``process:`` envelope but a non-``.process.md`` suffix
    exists on disk (so the 404 path doesn't fire) but fails the resolver's suffix
    gate. Must surface as a friendly 400, not a 500.
    """

    _write(
        tmp_path,
        "legacy.md",
        "---\nprocess:\n  name: p\n---\n\nbody\n",
    )
    monkeypatch.setattr(pb, "ROOT_DIR", tmp_path)

    resp = _run_api_viz(pb, {"process": "legacy.md"})
    assert resp.status_code == 400
    payload = json.loads(bytes(resp.body))
    assert "process spec path must end in" in payload["detail"]


def test_structure_report_sidekick_returns_process_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:

    _write(
        tmp_path,
        "test.process.md",
        "---\nprocess:\n  name: p\n  steps:\n    - id: s1\n      mode: code\n"
        "      handler: handler.py:noop\n      outputs:\n        out:\n"
        "          path: out.md\n          format: frontmatter-md\n---\n",
    )
    monkeypatch.setattr(pb, "ROOT_DIR", tmp_path)

    resp = sidekick.structure_report_handler(
        cast(Any, _FakeRequest({"process": "test.process.md"}))
    )
    payload = json.loads(bytes(resp.body))

    assert resp.status_code == 200
    assert payload["structure_report"]["schema"] == "metaproc:StructureReport/v1"
    assert payload["structure_report"]["summary"]["artifacts"] == 1


def test_resources_json_classified_as_resource_report(tmp_path: Path) -> None:
    """resources.json with the v1 schema gets the resource-report kind via the
    metaproc plugin's manifest classifier."""

    fixture = tmp_path / "resources.json"
    fixture.write_text('{"schema":"metaproc.resources/v1"}')

    assert _classify_with_plugins(fixture, ".json") == "resource-report"
    # First view is the default Resources tab (declared in plugin manifest).
    assert _views_for_kind("resource-report")[0]["id"] == "resources"


def test_resources_json_with_wrong_schema_falls_back_to_text(tmp_path: Path) -> None:
    fixture = tmp_path / "resources.json"
    fixture.write_text('{"schema":"something/else"}')
    assert classify_file_kind(fixture, ".json") == "text"


def test_arbitrary_json_file_not_classified_as_resource_report(tmp_path: Path) -> None:
    """Only files literally named resources.json should match (avoids false positives)."""
    fixture = tmp_path / "config.json"
    fixture.write_text('{"schema":"metaproc.resources/v1"}')
    assert classify_file_kind(fixture, ".json") == "text"
