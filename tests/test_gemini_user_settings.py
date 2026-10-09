"""Explicit Gemini homes isolate settings without moving task artifacts."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from metaproc.adapters.gemini_cli import GeminiCliAdapter
from metaproc.commands.run_process import RunExecutionContext, _prepare_composite_scope
from metaproc.engine.build_plan import build_plan
from metaproc.models.authored import ProcessSpec

pytestmark = pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
    reason="Private Gemini homes require POSIX directory descriptors",
)


def _template(tmp_path: Path) -> Path:
    home = tmp_path / "template"
    (home / ".gemini" / "policies").mkdir(parents=True)
    (home / ".gemini" / "settings.json").write_text(
        json.dumps(
            {
                "security": {"auth": {"selectedType": "vertex-ai"}},
                "hooks": {"BeforeTool": [{"matcher": "read_file", "hooks": []}]},
            }
        )
    )
    (home / ".gemini" / "GEMINI.md").write_text("Preserve operator instructions.\n")
    (home / ".gemini" / "policies" / "operator.toml").write_text("# operator policy\n")
    script = home / "operator-hook"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o700)
    return home


def _config(template: Path, cwd: Path) -> dict[str, object]:
    return {
        "native_settings_scope": "user",
        "native_settings_home_template": str(template),
        "working_directory": str(cwd),
        "native_settings": {"tools": {"core": ["read_file"]}},
    }


def test_user_settings_preserve_operator_assets_environment_and_cwd(tmp_path: Path) -> None:
    template = _template(tmp_path)
    before = (template / ".gemini" / "settings.json").read_bytes()
    cwd = tmp_path / "task"
    cwd.mkdir()
    (cwd / "input.txt").write_text("relative artifact")
    config = _config(template, cwd)
    original = {
        "HOME": str(tmp_path / "operator"),
        "GOOGLE_CLOUD_PROJECT": "example-project",
        "GEMINI_CLI_SYSTEM_SETTINGS_PATH": "/etc/example-admin-settings.json",
    }
    adapter = GeminiCliAdapter()
    env = adapter.prepare_env(original, config)
    assert adapter.validate_config(config) == []
    assert original == {key: env[key] for key in original}
    assert "GEMINI_CLI_HOME" not in original
    isolated = Path(env["GEMINI_CLI_HOME"])
    assert isolated != template
    settings = json.loads((isolated / ".gemini" / "settings.json").read_text())
    assert settings["security"]["auth"]["selectedType"] == "vertex-ai"
    assert settings["hooks"] == json.loads(before)["hooks"]
    assert settings["tools"]["core"] == ["read_file"]
    assert settings["general"]["sessionRetention"]["enabled"] is False
    for relative in (".gemini/GEMINI.md", ".gemini/policies/operator.toml", "operator-hook"):
        assert (isolated / relative).read_bytes() == (template / relative).read_bytes()
    assert stat.S_IMODE(isolated.stat().st_mode) == 0o700
    assert stat.S_IMODE((isolated / ".gemini/settings.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((isolated / "operator-hook").stat().st_mode) == 0o700
    assert adapter.working_directory(config) == cwd
    assert (cwd / "input.txt").read_text() == "relative artifact"
    assert not (cwd / ".gemini").exists()
    assert (template / ".gemini/settings.json").read_bytes() == before


def test_user_settings_are_private_for_concurrent_different_steps(tmp_path: Path) -> None:
    template = _template(tmp_path)
    configs = [_config(template, tmp_path) for _ in range(2)]
    configs[1]["native_settings"] = {"tools": {"core": ["write_file"]}}
    with ThreadPoolExecutor(max_workers=2) as executor:
        envs = list(executor.map(lambda c: GeminiCliAdapter().prepare_env({}, c), configs))
    homes = [Path(env["GEMINI_CLI_HOME"]) for env in envs]
    assert homes[0] != homes[1]
    assert [
        json.loads((home / ".gemini/settings.json").read_text())["tools"]["core"] for home in homes
    ] == [["read_file"], ["write_file"]]


def test_user_settings_template_is_optional_and_needs_user_scope(tmp_path: Path) -> None:
    adapter = GeminiCliAdapter()
    assert adapter.validate_config({"native_settings_scope": "user"}) == []
    assert adapter.validate_config({"native_settings_home_template": str(tmp_path)}) == []
    errors = adapter.validate_config(
        {
            "native_settings_scope": "workspace",
            "working_directory": str(tmp_path),
            "native_settings_home_template": str(tmp_path),
        }
    )
    assert any("requires native_settings_scope 'user'" in error.reason for error in errors)


def test_user_settings_reject_symlink_templates_and_assets(tmp_path: Path) -> None:
    template = _template(tmp_path)
    linked = tmp_path / "linked"
    linked.symlink_to(template, target_is_directory=True)
    adapter = GeminiCliAdapter()
    with pytest.raises(ValueError, match="symlink"):
        adapter.prepare_env({}, _config(linked, tmp_path))
    (template / ".gemini" / "linked-policy").symlink_to(template / ".gemini/policies")
    with pytest.raises(ValueError, match="symlink"):
        adapter.prepare_env({}, _config(template, tmp_path))


def test_user_settings_reject_invalid_template_settings(tmp_path: Path) -> None:
    template = _template(tmp_path)
    settings = template / ".gemini/settings.json"
    settings.write_text("[]")
    with pytest.raises(ValueError, match="JSON object"):
        GeminiCliAdapter().prepare_env({}, _config(template, tmp_path))
    settings.unlink()
    with pytest.raises(ValueError, match="settings.json"):
        GeminiCliAdapter().prepare_env({}, _config(template, tmp_path))


def test_user_settings_refuse_copy_collision_and_remove_partial_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(tmp_path)
    original_open = Path.open
    staged_homes: list[Path] = []

    def collide(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if mode == "xb" and path.name == "GEMINI.md":
            staged_homes.append(path.parent.parent)
            with original_open(path, "wb") as output:
                output.write(b"existing private asset")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", collide)
    with pytest.raises(FileExistsError):
        GeminiCliAdapter().prepare_env({}, _config(template, tmp_path))
    assert len(staged_homes) == 1
    assert not staged_homes[0].exists()
    assert (template / ".gemini/GEMINI.md").read_text() == "Preserve operator instructions.\n"


def test_user_settings_profile_reaches_composite_child_without_copying_specs(
    tmp_path: Path,
) -> None:
    template = _template(tmp_path)
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        json.dumps(
            {
                "profiles": {
                    "private-gemini": {
                        "adapter": "gemini-cli",
                        "config": _config(template, tmp_path),
                    }
                }
            }
        )
    )
    child = tmp_path / "child.process.md"
    child.write_text(
        "---\n"
        + json.dumps(
            {
                "process": {
                    "name": "child",
                    "steps": [
                        {
                            "id": "read",
                            "mode": "agent",
                            "prompt_prefix": "Read inputs.",
                            "output_root": "{{run.dir}}/result",
                        }
                    ],
                }
            }
        )
        + "\n---\n"
    )
    parent = ProcessSpec.model_validate(
        {
            "name": "parent",
            "deps": {"child": {"path": str(child), "as": "path"}},
            "steps": [{"id": "child", "mode": "composite", "uses": "deps.child"}],
        }
    )
    variables = {"RUNS_DIR": str(tmp_path), "RUN_ID": "example-run"}
    plan = build_plan(
        parent,
        variables,
        process_path=tmp_path / "parent.process.md",
        adapter_override="private-gemini",
        profile_files=[profiles],
    )
    with closing(
        RunExecutionContext.create(max_concurrency=1, profile_files=[profiles])
    ) as context:
        prepared = _prepare_composite_scope(
            step_def=parent.steps[0],
            target=plan.steps[0],
            variables=variables,
            run_dir=tmp_path / "example-run",
            run_id="example-run",
            scope_path=(),
            execution_context=context,
            scope_execution_profile="private-gemini",
            announce=False,
            out=SimpleNamespace(),
        )
    config = prepared.plan.steps[0].adapter.config
    assert config["native_settings_scope"] == "user"
    assert config["native_settings_home_template"] == str(template)
    assert config["working_directory"] == str(tmp_path)
