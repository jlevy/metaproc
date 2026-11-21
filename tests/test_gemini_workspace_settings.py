"""Opt-in native settings must reach Gemini without a root-owned settings file."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import cast

import pytest

from metaproc.adapters.gemini_cli import GeminiCliAdapter
from metaproc.io import write_secret_text
from metaproc.settings import GEMINI_DEFAULT_NATIVE_SETTINGS


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
    assert settings["experimental"]["dynamicModelConfiguration"] is False
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


# What v0.6.1 wrote for a workspace-scope step whose native_settings set `tools.core`,
# copied from that release rather than derived, so the test pins the released bytes.
_V061_WORKSPACE_SETTINGS: dict[str, object] = {
    "agents": {
        "overrides": {
            "cli_help": {"enabled": False},
            "codebase_investigator": {"enabled": False},
            "generalist": {"enabled": False},
        }
    },
    "context": {"fileFiltering": {"respectGitIgnore": False}},
    "experimental": {"dynamicModelConfiguration": True},
    "general": {"sessionRetention": {"enabled": False}},
    "modelConfigs": {
        "overrides": [
            {
                "match": {"overrideScope": "chat-base-3"},
                "modelConfig": {
                    "generateContentConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}}
                },
            }
        ]
    },
    "tools": {"core": ["read_file"]},
}
_STEP_TOOLS: dict[str, object] = {"tools": {"core": ["read_file"]}}


def _merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge(
                cast("dict[str, object]", existing), cast("dict[str, object]", value)
            )
        else:
            merged[key] = value
    return merged


def _workspace_config(
    directory: Path, native_settings: dict[str, object] | None = None
) -> dict[str, object]:
    config: dict[str, object] = {
        "native_settings_scope": "workspace",
        "working_directory": str(directory),
    }
    if native_settings is not None:
        config["native_settings"] = native_settings
    return config


def _write_settings(directory: Path, settings: dict[str, object]) -> Path:
    path = directory / ".gemini/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _fresh_settings(tmp_path: Path, native_settings: dict[str, object] | None) -> str:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    GeminiCliAdapter().prepare_env({}, _workspace_config(fresh, native_settings))
    return (fresh / ".gemini/settings.json").read_text(encoding="utf-8")


def test_workspace_settings_from_an_earlier_release_are_replaced_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A relaunch of a run v0.6.1 started proceeds; only Metaproc's moved defaults change."""
    caplog.set_level(logging.WARNING, logger="metaproc.adapters.gemini_cli")
    workspace = tmp_path / "scope"
    settings_path = _write_settings(workspace, _V061_WORKSPACE_SETTINGS)

    GeminiCliAdapter().prepare_env({}, _workspace_config(workspace, _STEP_TOOLS))

    assert settings_path.read_text(encoding="utf-8") == _fresh_settings(tmp_path, _STEP_TOOLS)
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["experimental"]["dynamicModelConfiguration"] is False
    assert settings["modelConfigs"] == GEMINI_DEFAULT_NATIVE_SETTINGS["modelConfigs"]
    assert settings["tools"] == {"core": ["read_file"]}
    warning = caplog.text
    assert str(settings_path) in warning
    assert "experimental.dynamicModelConfiguration" in warning
    assert "modelConfigs.overrides" in warning
    assert "tools.core" not in warning
    assert "general.sessionRetention.enabled" not in warning
    assert (workspace / ".gemini/.metaproc-settings.json").is_file()


@pytest.mark.parametrize(
    ("operator_settings", "named", "not_named"),
    [
        pytest.param(
            {"ui": {"theme": "Default"}}, "ui.theme", "dynamicModelConfiguration", id="new-key"
        ),
        pytest.param(
            {"general": {"sessionRetention": {"enabled": True}}},
            "general.sessionRetention.enabled",
            "dynamicModelConfiguration",
            id="metaproc-key",
        ),
    ],
)
def test_workspace_settings_from_an_earlier_release_with_operator_edits_are_refused(
    tmp_path: Path, operator_settings: dict[str, object], named: str, not_named: str
) -> None:
    """An operator's value in an old Metaproc file is not Metaproc's to replace."""
    workspace = tmp_path / "scope"
    edited = _merge(_V061_WORKSPACE_SETTINGS, operator_settings)
    settings_path = _write_settings(workspace, edited)
    original = settings_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="existing Gemini workspace settings") as refusal:
        GeminiCliAdapter().prepare_env({}, _workspace_config(workspace, _STEP_TOOLS))

    message = str(refusal.value)
    assert named in message
    assert not_named not in message
    assert "delete the file" in message
    assert settings_path.read_text(encoding="utf-8") == original
    assert not (workspace / ".gemini/.metaproc-settings.json").exists()


def test_workspace_settings_unchanged_are_not_rewritten(tmp_path: Path) -> None:
    """A retry with the same settings leaves the same file, and its record, in place."""
    adapter = GeminiCliAdapter()
    config = _workspace_config(tmp_path, _STEP_TOOLS)
    adapter.prepare_env({}, config)
    paths = [tmp_path / ".gemini/settings.json", tmp_path / ".gemini/.metaproc-settings.json"]
    before = [(path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()) for path in paths]

    adapter.prepare_env({}, config)

    after = [(path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()) for path in paths]
    assert after == before


def test_workspace_settings_replace_a_record_with_invalid_utf8(tmp_path: Path) -> None:
    adapter = GeminiCliAdapter()
    config = _workspace_config(tmp_path, _STEP_TOOLS)
    adapter.prepare_env({}, config)
    settings_path = tmp_path / ".gemini/settings.json"
    record_path = tmp_path / ".gemini/.metaproc-settings.json"
    settings, record = settings_path.read_bytes(), record_path.read_bytes()
    record_path.write_bytes(b"\xc3")

    adapter.prepare_env({}, config)

    assert settings_path.read_bytes() == settings
    assert record_path.read_bytes() == record


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
def test_workspace_settings_leave_a_non_regular_record_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _workspace_config(tmp_path)
    adapter = GeminiCliAdapter()
    adapter.prepare_env({}, config)
    record_path = tmp_path / ".gemini/.metaproc-settings.json"
    record_path.unlink()
    os.mkfifo(record_path)
    read_text = Path.read_text

    def guarded_read_text(
        path: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        assert path != record_path, "a plain text read could block on the FIFO"
        return read_text(path, encoding, errors)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    assert adapter.prepare_env({}, config) == {}
    assert stat.S_ISFIFO(record_path.lstat().st_mode)


def _with_changed_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for a later release whose catalog adds a model needing its own override."""
    model_configs = cast("dict[str, list[object]]", GEMINI_DEFAULT_NATIVE_SETTINGS["modelConfigs"])
    added = {
        "match": {"model": "gemini-9-flash"},
        "modelConfig": {"generateContentConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}}},
    }
    changed = {
        **GEMINI_DEFAULT_NATIVE_SETTINGS,
        "modelConfigs": {"overrides": [*model_configs["overrides"], added]},
    }
    monkeypatch.setattr("metaproc.adapters.gemini_cli.GEMINI_DEFAULT_NATIVE_SETTINGS", changed)


def test_workspace_settings_bound_the_companion_read_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _workspace_config(tmp_path)
    adapter = GeminiCliAdapter()
    adapter.prepare_env({}, config)
    settings_path = tmp_path / ".gemini/settings.json"
    record_path = tmp_path / ".gemini/.metaproc-settings.json"
    settings, record = settings_path.read_bytes(), record_path.read_bytes()
    oversized = record + b" "
    record_path.write_bytes(oversized)
    monkeypatch.setattr("metaproc.adapters.gemini_cli._MAX_WORKSPACE_RECORD_BYTES", len(record))

    with monkeypatch.context() as changed_catalog:
        _with_changed_default(changed_catalog)
        with pytest.raises(ValueError, match=r"modelConfigs\.overrides"):
            adapter.prepare_env({}, config)
    assert settings_path.read_bytes() == settings
    assert record_path.read_bytes() == oversized

    adapter.prepare_env({}, config)

    assert settings_path.read_bytes() == settings
    assert record_path.read_bytes() == record


def test_workspace_settings_record_lets_a_later_default_change_replace_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="metaproc.adapters.gemini_cli")
    config = _workspace_config(tmp_path, _STEP_TOOLS)
    GeminiCliAdapter().prepare_env({}, config)
    _with_changed_default(monkeypatch)

    GeminiCliAdapter().prepare_env({}, config)

    settings = json.loads((tmp_path / ".gemini/settings.json").read_text(encoding="utf-8"))
    assert settings["modelConfigs"]["overrides"][-1]["match"] == {"model": "gemini-9-flash"}
    assert settings["tools"] == {"core": ["read_file"]}
    assert "modelConfigs.overrides" in caplog.text
    record = json.loads((tmp_path / ".gemini/.metaproc-settings.json").read_text())
    assert record["metaproc_default_native_settings"]["modelConfigs"] == settings["modelConfigs"]


def test_workspace_settings_without_their_record_are_not_claimed_after_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the record, only the defaults of releases that kept none count as Metaproc's."""
    config = _workspace_config(tmp_path, _STEP_TOOLS)
    GeminiCliAdapter().prepare_env({}, config)
    (tmp_path / ".gemini/.metaproc-settings.json").unlink()
    _with_changed_default(monkeypatch)

    with pytest.raises(ValueError, match=r"modelConfigs\.overrides"):
        GeminiCliAdapter().prepare_env({}, config)


def test_workspace_settings_edited_after_metaproc_wrote_them_are_refused(tmp_path: Path) -> None:
    """The record holds today's value, so an operator's edit to a Metaproc key stands."""
    config = _workspace_config(tmp_path)
    GeminiCliAdapter().prepare_env({}, config)
    settings_path = tmp_path / ".gemini/settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["experimental"]["dynamicModelConfiguration"] = True
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    with pytest.raises(ValueError, match=r"experimental\.dynamicModelConfiguration"):
        GeminiCliAdapter().prepare_env({}, config)
    assert json.loads(settings_path.read_text())["experimental"]["dynamicModelConfiguration"]


@pytest.mark.parametrize(
    ("operator_settings", "path"),
    [
        pytest.param(
            {"security": {"auth": {"selectedType": "vertex-ai"}}},
            "security.auth.selectedType",
            id="operator-section",
        ),
        pytest.param(
            {"general": {"sessionRetention": {"maxAge": "2d"}}},
            "general.sessionRetention.maxAge",
            id="nested-operator-key",
        ),
        pytest.param(
            {"experimental": {"dynamicModelConfiguration": True}},
            "experimental.dynamicModelConfiguration",
            id="mixed-default-generations",
        ),
        pytest.param(
            {"modelConfigs": {"overrides": [{"match": {"model": "chat-base-3"}}]}},
            "modelConfigs.overrides",
            id="operator-model-override",
        ),
    ],
)
def test_workspace_record_cannot_claim_operator_edits(
    tmp_path: Path, operator_settings: dict[str, object], path: str
) -> None:
    config = _workspace_config(tmp_path)
    adapter = GeminiCliAdapter()
    adapter.prepare_env({}, config)
    settings_path = tmp_path / ".gemini/settings.json"
    record_path = tmp_path / ".gemini/.metaproc-settings.json"
    settings = _merge(json.loads(settings_path.read_text()), operator_settings)
    recorded = json.loads(record_path.read_text())["metaproc_default_native_settings"]
    _write_settings(tmp_path, settings)
    record_path.write_text(
        json.dumps({"metaproc_default_native_settings": _merge(recorded, operator_settings)}),
        encoding="utf-8",
    )
    before = (settings_path.read_bytes(), record_path.read_bytes())

    with pytest.raises(ValueError, match="existing Gemini workspace settings") as refusal:
        adapter.prepare_env({}, config)

    assert path in str(refusal.value)
    assert (settings_path.read_bytes(), record_path.read_bytes()) == before


def test_workspace_settings_refuse_a_record_from_other_defaults(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path)
    adapter = GeminiCliAdapter()
    adapter.prepare_env({}, config)
    settings_path = tmp_path / ".gemini/settings.json"
    record_path = tmp_path / ".gemini/.metaproc-settings.json"
    settings = json.loads(settings_path.read_text())
    settings["experimental"]["dynamicModelConfiguration"] = True
    _write_settings(tmp_path, settings)
    earlier_defaults = {
        key: value for key, value in _V061_WORKSPACE_SETTINGS.items() if key != "tools"
    }
    record_path.write_text(
        json.dumps({"metaproc_default_native_settings": earlier_defaults}), encoding="utf-8"
    )
    before = (settings_path.read_bytes(), record_path.read_bytes())

    with pytest.raises(ValueError, match=r"modelConfigs\.overrides"):
        adapter.prepare_env({}, config)

    assert (settings_path.read_bytes(), record_path.read_bytes()) == before


def test_workspace_settings_preserve_an_empty_operator_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _workspace_config(tmp_path)
    adapter = GeminiCliAdapter()
    adapter.prepare_env({}, config)
    settings_path = tmp_path / ".gemini/settings.json"
    settings = json.loads(settings_path.read_text())
    settings["mcpServers"] = {}
    _write_settings(tmp_path, settings)
    before = settings_path.read_bytes()
    _with_changed_default(monkeypatch)

    with pytest.raises(ValueError, match="mcpServers"):
        adapter.prepare_env({}, config)

    assert settings_path.read_bytes() == before


def test_workspace_settings_of_a_step_with_other_native_settings_are_refused(
    tmp_path: Path,
) -> None:
    """Two steps sharing a directory cannot take turns rewriting each other's settings."""
    GeminiCliAdapter().prepare_env(
        {}, _workspace_config(tmp_path, {"experimental": {"dynamicModelConfiguration": True}})
    )

    with pytest.raises(ValueError, match=r"experimental\.dynamicModelConfiguration"):
        GeminiCliAdapter().prepare_env({}, _workspace_config(tmp_path))
