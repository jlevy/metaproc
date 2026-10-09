"""Metaproc's Gemini settings must reach a CLI that refuses user-owned system settings.

Since gemini-cli 0.60.0, `loadSystemFile` in `packages/cli/src/config/settings.ts` reads
the file `GEMINI_CLI_SYSTEM_SETTINGS_PATH` names only when `isFileAndDirectorySecureSync`
(`packages/core/src/utils/security.ts`) passes: the file and every directory above it
must be owned by root and writable by neither group nor others. Otherwise the CLI logs
`Security Warning: Skipping system settings file ...` and runs without it, so every
setting in it is lost without an error. User settings, read from
`$GEMINI_CLI_HOME/.gemini/settings.json` (`$HOME` when unset), and the settings of a
trusted workspace carry no ownership rule.

`_settings_gemini_loads` applies those rules to the environment and working directory
Metaproc launches Gemini with.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import cast

import pytest

from metaproc.adapters.gemini_cli import GeminiCliAdapter
from metaproc.settings import GEMINI_DEFAULT_NATIVE_SETTINGS

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Gemini's ownership rule is the POSIX one"
)


def _root_ownership_refusal(path: Path) -> str | None:
    """Why gemini-cli 0.60+ skips `path` as a system settings file, or None if it loads it."""
    for candidate in (path, *path.parents):
        metadata = candidate.stat()
        if metadata.st_uid != 0:
            return f"{candidate} is not owned by root (uid {metadata.st_uid})"
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{candidate} is writable by group or others"
    resolved = path.resolve()
    return None if resolved == path else _root_ownership_refusal(resolved)


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


def _settings_gemini_loads(
    env: dict[str, str], cwd: Path | None
) -> tuple[dict[str, object], list[str]]:
    """The settings gemini-cli 0.60+ merges for this launch, and the files it skips.

    Precedence is user, then trusted workspace (`--skip-trust`), then system.
    """
    merged: dict[str, object] = {}
    skipped: list[str] = []
    home = Path(env.get("GEMINI_CLI_HOME") or env["HOME"])
    candidates = [home / ".gemini" / "settings.json"]
    if cwd is not None:
        candidates.append(cwd / ".gemini" / "settings.json")
    for candidate in candidates:
        if candidate.is_file():
            merged = _merge(merged, json.loads(candidate.read_text(encoding="utf-8")))
    system = env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
    if system and Path(system).is_file():
        refusal = _root_ownership_refusal(Path(system))
        if refusal is None:
            merged = _merge(merged, json.loads(Path(system).read_text(encoding="utf-8")))
        else:
            skipped.append(f"Skipping system settings file {system}: {refusal}")
    return merged, skipped


def _operator_home(tmp_path: Path) -> Path:
    """An operator home with the mutable state a launch must not borrow."""
    home = tmp_path / "operator"
    gemini = home / ".gemini"
    gemini.mkdir(parents=True)
    (gemini / "settings.json").write_text(
        json.dumps(
            {
                "general": {"sessionRetention": {"enabled": True}},
                "security": {"auth": {"selectedType": "oauth-personal"}},
                "hooks": {"BeforeTool": [{"hooks": [{"type": "command", "command": "true"}]}]},
            }
        ),
        encoding="utf-8",
    )
    (gemini / "oauth_creds.json").write_text('{"refresh_token": "operator"}\n', encoding="utf-8")
    (gemini / "projects.json").write_text('{"projects": {}}\n', encoding="utf-8")
    (gemini / "GEMINI.md").write_text("Operator instructions.\n", encoding="utf-8")
    return home


def test_default_native_settings_reach_gemini_without_a_root_owned_file(tmp_path: Path) -> None:
    """A profile that names no scope still gets every Metaproc setting into the CLI."""
    environment = {
        "HOME": str(_operator_home(tmp_path)),
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "example-project",
        "GOOGLE_CLOUD_LOCATION": "global",
    }
    config: dict[str, object] = {"native_settings": {"tools": {"core": ["read_file"]}}}
    adapter = GeminiCliAdapter()
    assert adapter.validate_config(config) == []

    env = adapter.prepare_env(environment, config)
    loaded, skipped = _settings_gemini_loads(env, adapter.working_directory(config))

    assert skipped == []
    general = cast("dict[str, dict[str, object]]", loaded["general"])
    assert general["sessionRetention"]["enabled"] is False
    assert loaded["experimental"] == {"dynamicModelConfiguration": True}
    assert loaded["tools"] == {"core": ["read_file"]}
    assert loaded["agents"] == GEMINI_DEFAULT_NATIVE_SETTINGS["agents"]
    assert loaded["modelConfigs"] == GEMINI_DEFAULT_NATIVE_SETTINGS["modelConfigs"]


def test_default_home_holds_only_metaproc_settings(tmp_path: Path) -> None:
    """The default private home borrows no operator state: no hooks, login or registry.

    Authentication stays with the launch environment, which Gemini reads when the
    settings name no auth type.
    """
    operator = _operator_home(tmp_path)
    environment = {
        "HOME": str(operator),
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "example-project",
    }
    adapter = GeminiCliAdapter()
    first = adapter.prepare_env(environment, {})
    second = adapter.prepare_env(environment, {})

    assert {key: first[key] for key in environment} == environment
    homes = [Path(first["GEMINI_CLI_HOME"]), Path(second["GEMINI_CLI_HOME"])]
    assert homes[0] != homes[1]
    for home in homes:
        assert not home.is_relative_to(operator)
        assert sorted(
            str(path.relative_to(home)) for path in home.rglob("*") if path.is_file()
        ) == [".gemini/settings.json"]
        settings = json.loads((home / ".gemini" / "settings.json").read_text(encoding="utf-8"))
        assert settings == GEMINI_DEFAULT_NATIVE_SETTINGS
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert stat.S_IMODE((home / ".gemini").stat().st_mode) == 0o700
        assert stat.S_IMODE((home / ".gemini" / "settings.json").stat().st_mode) == 0o600


def test_system_scope_is_refused_because_gemini_would_skip_it(tmp_path: Path) -> None:
    """An explicit system scope fails loudly instead of losing its settings in a warning."""
    config: dict[str, object] = {"native_settings_scope": "system"}
    adapter = GeminiCliAdapter()

    rejections = adapter.validate_config(config)

    assert [rejection.key for rejection in rejections] == ["native_settings_scope"]
    assert "owned by root" in rejections[0].reason
    assert "'user'" in rejections[0].reason
    with pytest.raises(ValueError, match="owned by root"):
        adapter.prepare_env({"HOME": str(tmp_path)}, config)
