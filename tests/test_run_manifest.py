"""Tests for the RunManifest Pydantic model and its CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.models.run_manifest import RunManifest

runner = CliRunner()


# dict[str, Any] rather than the project's usual dict[str, object] so the spread
# pattern RunManifest(**VALID_MANIFEST) stays typed — Pydantic's __init__ takes
# heterogeneous typed kwargs that need Any to flow cleanly.
VALID_MANIFEST: dict[str, Any] = {
    "run_id": "baseline-100-glm5-2026-04-17-p1a",
    "stage_class": "baseline",
    "dataset": "tech-mix-100",
    "git_sha": "0123456789abcdef0123456789abcdef01234567",
    "image": "sha256:" + "0" * 64,
    "models": ["pi-glm-5"],
    "cost_tier": "free-vertex-maas",
    "scoreboard": "plug-compatible",
    "runtime_arm": "canonical",
    "num_workers": 1,
    "max_concurrency": 15,
    "launch_flags": ["--no-spot", "--max-duration", "8h"],
}


class TestRunManifestValidation:
    def test_valid_manifest_round_trip(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        path.write_text(yaml.safe_dump(VALID_MANIFEST))
        manifest = RunManifest.from_yaml_file(path)
        assert manifest.run_id == "baseline-100-glm5-2026-04-17-p1a"
        assert manifest.models == ["pi-glm-5"]

        # Write back and re-load — equality holds through canonicalization.
        roundtrip_path = tmp_path / "manifest.canonical.yaml"
        manifest.to_yaml(roundtrip_path)
        reloaded = RunManifest.from_yaml_file(roundtrip_path)
        assert reloaded == manifest

    def test_canonical_field_order(self, tmp_path: Path):
        manifest = RunManifest(**VALID_MANIFEST)
        text = manifest.to_yaml()
        # run_id comes first in the canonical order.
        first_key = text.splitlines()[0].split(":")[0]
        assert first_key == "run_id"

    def test_rejects_unknown_stage_class(self):
        bad = dict(VALID_MANIFEST, stage_class="phase-0c-blocker")
        with pytest.raises(ValidationError):
            RunManifest(**bad)

    @pytest.mark.parametrize(
        "stage_class",
        [
            "promotion-1000",
            "promotion-nasdaq",
            "predict-atoms",
            "predict-bakeoff",
            "predict-promotion",
        ],
    )
    def test_rejects_earnings_specific_stage_classes(self, stage_class: str):
        bad = dict(VALID_MANIFEST, stage_class=stage_class)
        with pytest.raises(ValidationError):
            RunManifest(**bad)

    def test_rejects_unknown_cost_tier(self):
        bad = dict(VALID_MANIFEST, cost_tier="free-lunch")
        with pytest.raises(ValidationError):
            RunManifest(**bad)

    def test_rejects_unknown_extra_fields(self):
        # extra="forbid" — unknown keys should fail validation.
        bad = dict(VALID_MANIFEST, nonsense_field="hi")
        with pytest.raises(ValidationError):
            RunManifest(**bad)

    def test_rejects_zero_concurrency(self):
        bad = dict(VALID_MANIFEST, max_concurrency=0)
        with pytest.raises(ValidationError):
            RunManifest(**bad)

    def test_empty_models_list_rejected(self):
        bad = dict(VALID_MANIFEST, models=[])
        with pytest.raises(ValidationError):
            RunManifest(**bad)

    def test_dual_model_pair_accepted(self):
        ok = dict(VALID_MANIFEST, models=["pi-glm-5", "pi-deepseek-v3.2"])
        manifest = RunManifest(**ok)
        assert len(manifest.models) == 2


class TestRunManifestCLI:
    def test_validate_prints_canonical(self, tmp_path: Path):
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump(VALID_MANIFEST))
        result = runner.invoke(app, ["run-manifest", str(path)])
        assert result.exit_code == 0
        assert "run_id:" in result.output

    def test_rewrite_normalizes_file(self, tmp_path: Path):
        path = tmp_path / "m.yaml"
        # Write an intentionally scrambled field order.
        scrambled = {
            "launch_flags": ["--no-spot"],
            "max_concurrency": 15,
            "num_workers": 1,
            "runtime_arm": "canonical",
            "scoreboard": "plug-compatible",
            "cost_tier": "free-vertex-maas",
            "models": ["pi-glm-5"],
            "image": VALID_MANIFEST["image"],
            "git_sha": VALID_MANIFEST["git_sha"],
            "dataset": "tech-mix-100",
            "stage_class": "baseline",
            "run_id": "scrambled",
        }
        path.write_text(yaml.safe_dump(scrambled, sort_keys=False))

        result = runner.invoke(app, ["run-manifest", str(path), "--rewrite"])
        assert result.exit_code == 0
        rewritten = path.read_text()
        # Canonical form puts run_id first.
        assert rewritten.splitlines()[0].startswith("run_id:")

    def test_invalid_file_exits_nonzero(self, tmp_path: Path):
        path = tmp_path / "m.yaml"
        bad = dict(VALID_MANIFEST, stage_class="phase-0c")
        path.write_text(yaml.safe_dump(bad))
        result = runner.invoke(app, ["run-manifest", str(path)])
        assert result.exit_code == 1
        assert "validation" in result.output.lower() or "Error" in result.output

    def test_missing_file_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, ["run-manifest", str(tmp_path / "nope.yaml")])
        assert result.exit_code == 1
        assert "does not exist" in result.output
