"""Opt-in native settings must reach Gemini without a root-owned settings file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.adapters.gemini_cli import GeminiCliAdapter
from metaproc.io import write_secret_text


def test_workspace_settings_preserve_environment_and_native_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = GeminiCliAdapter()
    config: dict[str, object] = {
        "native_settings_scope": "workspace",
        "working_directory": str(tmp_path),
        "native_settings": {"tools": {"core": ["read_file", "write_file"]}},
    }
    original = {"HOME": "/operator", "GOOGLE_CLOUD_PROJECT": "test-project"}
    env = adapter.prepare_env(original, config)
    assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" not in env
    assert env == original
    settings = json.loads((tmp_path / ".gemini/settings.json").read_text())
    assert settings["general"]["sessionRetention"]["enabled"] is False
    assert settings["experimental"]["dynamicModelConfiguration"] is True
    assert settings["tools"]["core"] == ["read_file", "write_file"]
    assert adapter.prepare_env(original, config) == env
    assert adapter.validate_config(config) == []
    monkeypatch.setenv("METAPROC_SKIP_GEMINI_VERSION_CHECK", "1")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Review retained inputs.")
    assert "--skip-trust" in adapter.build_command(prompt, config, {})


def test_workspace_settings_refuse_existing_conflict(tmp_path: Path) -> None:
    existing = tmp_path / ".gemini/settings.json"
    existing.parent.mkdir()
    original = '{"security":{"auth":{"selectedType":"vertex-ai"}}}\n'
    existing.write_text(original)
    with pytest.raises(ValueError, match="existing Gemini workspace settings"):
        GeminiCliAdapter().prepare_env(
            {}, {"native_settings_scope": "workspace", "working_directory": str(tmp_path)}
        )
    assert existing.read_text() == original


def test_workspace_settings_require_explicit_directory() -> None:
    rejections = GeminiCliAdapter().validate_config({"native_settings_scope": "workspace"})
    assert any("requires working_directory" in row.reason for row in rejections)


def test_workspace_settings_do_not_follow_settings_symlink(tmp_path: Path) -> None:
    target = tmp_path / "external.json"
    target.write_text("{}")
    workspace = tmp_path / "scope"
    (workspace / ".gemini").mkdir(parents=True)
    (workspace / ".gemini/settings.json").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        GeminiCliAdapter().prepare_env(
            {}, {"native_settings_scope": "workspace", "working_directory": str(workspace)}
        )
    assert target.read_text() == "{}"


def test_workspace_settings_reject_unknown_scope() -> None:
    rejections = GeminiCliAdapter().validate_config({"native_settings_scope": "unknown"})
    assert any("must be 'user' or 'workspace'" in row.reason for row in rejections)


def test_workspace_settings_do_not_follow_directory_symlink(tmp_path: Path) -> None:
    target = tmp_path / "external"
    target.mkdir()
    workspace = tmp_path / "scope"
    workspace.mkdir()
    (workspace / ".gemini").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="directory must not be a symlink"):
        GeminiCliAdapter().prepare_env(
            {}, {"native_settings_scope": "workspace", "working_directory": str(workspace)}
        )
    assert not (target / "settings.json").exists()


def test_workspace_settings_refuse_file_created_during_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / ".gemini/settings.json"
    original = '{"operatorAuthored":true}\n'

    def write_with_collision(path: Path, content: str) -> None:
        settings_path.write_text(original, encoding="utf-8")
        write_secret_text(path, content)

    monkeypatch.setattr("metaproc.adapters.gemini_cli.write_secret_text", write_with_collision)
    with pytest.raises(ValueError, match="existing Gemini workspace settings"):
        GeminiCliAdapter().prepare_env(
            {}, {"native_settings_scope": "workspace", "working_directory": str(tmp_path)}
        )
    assert settings_path.read_text(encoding="utf-8") == original
    assert sorted(path.name for path in settings_path.parent.iterdir()) == ["settings.json"]
