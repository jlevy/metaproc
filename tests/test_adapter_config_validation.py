"""Planner-side adapter config validation regressions."""

from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path

import pytest
from pytest import LogCaptureFixture, MonkeyPatch

from metaproc.engine.build_plan import build_plan
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import Plan


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def _make_predict_like_spec() -> ProcessSpec:
    return ProcessSpec.model_validate(
        {
            "name": "test",
            "defaults": {
                "default_adapter": "claude-code-cli",
                "adapters": {
                    "claude-code-cli": {
                        "type": "claude-code-cli",
                        "config": {
                            "permission_mode": "bypassPermissions",
                            "output_format": "stream-json",
                            "timeout_s": 900,
                            "tools": ["Read", "Write", "Bash"],
                        },
                    }
                },
            },
            "steps": [
                {
                    "id": "extract-items",
                    "mode": "agent",
                    "prompt_prefix": "do thing",
                    "adapter": {
                        "type": "pi-cli",
                        "config": {
                            "output_format": "stream-json",
                            "timeout_s": 1800,
                            "no_session_persistence": True,
                        },
                        "config_by_variant": {
                            "pi-cli": {
                                "provider": "vertex-maas",
                                "model": "glm-5-maas",
                            }
                        },
                    },
                    "outputs": {"records": {"path": "records/", "kind": "directory"}},
                }
            ],
        }
    )


class TestAdapterConfigValidation:
    def test_variant_specific_step_config_applies_only_to_matching_variant(self) -> None:
        spec = _make_predict_like_spec()
        with tempfile.TemporaryDirectory() as tmpdir:
            process_path = Path(tmpdir) / "test.process.md"
            _write(process_path, "---\nprocess:\n  name: test\n---\n")
            plan = build_plan(
                spec, {}, process_path=process_path, adapter_override="claude-code-cli"
            )

        config = plan.steps[0].adapter.config
        assert config["permission_mode"] == "bypassPermissions"
        assert config["timeout_s"] == 1800
        assert config["no_session_persistence"] is True
        assert "provider" not in config
        assert "model" not in config

    def test_cross_family_invalid_shared_config_fails_at_plan_time(self) -> None:
        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "defaults": {
                    "default_adapter": "claude-code-cli",
                    "adapters": {
                        "claude-code-cli": {
                            "type": "claude-code-cli",
                            "config": {
                                "permission_mode": "bypassPermissions",
                                "output_format": "stream-json",
                            },
                        }
                    },
                },
                "steps": [
                    {
                        "id": "extract-items",
                        "mode": "agent",
                        "prompt_prefix": "do thing",
                        "adapter": {
                            "type": "pi-cli",
                            "config": {
                                "provider": "vertex-maas",
                                "model": "glm-5-maas",
                                "timeout_s": 1800,
                            },
                        },
                        "outputs": {"records": {"path": "records/", "kind": "directory"}},
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            process_path = Path(tmpdir) / "test.process.md"
            _write(process_path, "---\nprocess:\n  name: test\n---\n")
            with pytest.raises(ValueError, match="invalid adapter config"):
                build_plan(spec, {}, process_path=process_path, adapter_override="claude-code-cli")

    def test_same_family_invalid_key_warns_and_sanitizes_without_strict(
        self, caplog: LogCaptureFixture
    ) -> None:
        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "defaults": {
                    "default_adapter": "claude-code-cli",
                    "adapters": {
                        "claude-code-cli": {
                            "type": "claude-code-cli",
                            "config": {
                                "permission_mode": "bypassPermissions",
                                "output_format": "stream-json",
                            },
                        }
                    },
                },
                "steps": [
                    {
                        "id": "summarize",
                        "mode": "agent",
                        "prompt_prefix": "do thing",
                        "adapter": {
                            "type": "claude-code-cli",
                            "config": {
                                "provider": "vertex-maas",
                                "model": "opus",
                            },
                        },
                        "outputs": {"summary": {"path": "summary.md", "kind": "file"}},
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            process_path = Path(tmpdir) / "test.process.md"
            _write(process_path, "---\nprocess:\n  name: test\n---\n")
            plan = build_plan(spec, {}, process_path=process_path)

        config = plan.steps[0].adapter.config
        assert config["model"] == "opus"
        assert "provider" not in config
        assert "invalid adapter config" in caplog.text

    def test_same_family_invalid_key_raises_with_strict_env(self, monkeypatch: MonkeyPatch) -> None:
        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "defaults": {
                    "default_adapter": "claude-code-cli",
                    "adapters": {
                        "claude-code-cli": {
                            "type": "claude-code-cli",
                            "config": {
                                "permission_mode": "bypassPermissions",
                                "output_format": "stream-json",
                            },
                        }
                    },
                },
                "steps": [
                    {
                        "id": "summarize",
                        "mode": "agent",
                        "prompt_prefix": "do thing",
                        "adapter": {
                            "type": "claude-code-cli",
                            "config": {
                                "provider": "vertex-maas",
                                "model": "opus",
                            },
                        },
                        "outputs": {"summary": {"path": "summary.md", "kind": "file"}},
                    }
                ],
            }
        )
        monkeypatch.setenv("METAPROC_ADAPTER_STRICT", "1")
        with tempfile.TemporaryDirectory() as tmpdir:
            process_path = Path(tmpdir) / "test.process.md"
            _write(process_path, "---\nprocess:\n  name: test\n---\n")
            with pytest.raises(ValueError, match="invalid adapter config"):
                build_plan(spec, {}, process_path=process_path)


_GEMINI_PROFILE = "gemini-private"


def _gemini_plan(
    tmp_path: Path,
    *,
    profile_config: dict[str, object] | None = None,
    step_env: dict[str, str] | None = None,
) -> Plan:
    """Plan one Gemini agent step under a profile file, as a run would."""
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        json.dumps(
            {
                "profiles": {
                    _GEMINI_PROFILE: {
                        "adapter": "gemini-cli",
                        "config": profile_config or {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    spec = ProcessSpec.model_validate(
        {
            "name": "test",
            "steps": [
                {
                    "id": "summarize",
                    "mode": "agent",
                    "prompt_prefix": "do thing",
                    "env": step_env or {},
                    "outputs": {"summary": {"path": "summary.md", "kind": "file"}},
                }
            ],
        }
    )
    process_path = tmp_path / "test.process.md"
    _write(process_path, "---\nprocess:\n  name: test\n---\n")
    return build_plan(
        spec,
        {},
        process_path=process_path,
        adapter_override=_GEMINI_PROFILE,
        profile_files=[profiles],
    )


class TestGeminiPlanValidation:
    """A Gemini profile or step that would defeat the adapter's settings is caught at plan time."""

    def test_legacy_system_scope_is_dropped_with_a_warning(
        self, tmp_path: Path, caplog: LogCaptureFixture
    ) -> None:
        plan = _gemini_plan(tmp_path, profile_config={"native_settings_scope": "system"})

        assert "native_settings_scope" not in plan.steps[0].adapter.config
        assert "invalid adapter config for gemini-cli" in caplog.text
        assert "owned by root" in caplog.text

    def test_legacy_system_scope_is_refused_when_strict(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAPROC_ADAPTER_STRICT", "1")
        with pytest.raises(
            ValueError, match="invalid adapter config for gemini-cli.*owned by root"
        ):
            _gemini_plan(tmp_path, profile_config={"native_settings_scope": "system"})

    def test_step_env_naming_an_owned_variable_is_dropped_with_a_warning(
        self, tmp_path: Path, caplog: LogCaptureFixture
    ) -> None:
        plan = _gemini_plan(
            tmp_path,
            step_env={
                "GEMINI_CLI_HOME": "/elsewhere",
                "GEMINI_CLI_SYSTEM_SETTINGS_PATH": "/elsewhere/settings.json",
                "GEMINI_CLI_SYSTEM_DEFAULTS_PATH": "/elsewhere/defaults.json",
                "GEMINI_SYSTEM_MD": "/elsewhere/system.md",
                "EXAMPLE_FLAG": "1",
            },
        )

        assert plan.steps[0].env == {"EXAMPLE_FLAG": "1"}
        assert "invalid step env for gemini-cli" in caplog.text
        for key in (
            "GEMINI_CLI_HOME",
            "GEMINI_CLI_SYSTEM_SETTINGS_PATH",
            "GEMINI_CLI_SYSTEM_DEFAULTS_PATH",
            "GEMINI_SYSTEM_MD",
        ):
            assert key in caplog.text

    def test_step_env_naming_an_owned_variable_is_refused_when_strict(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAPROC_ADAPTER_STRICT", "1")
        with pytest.raises(ValueError, match="invalid step env for gemini-cli: GEMINI_CLI_HOME"):
            _gemini_plan(tmp_path, step_env={"GEMINI_CLI_HOME": "/elsewhere"})

    def test_workspace_scope_leaves_the_home_variable_to_the_step(
        self, tmp_path: Path, caplog: LogCaptureFixture
    ) -> None:
        """Workspace scope writes no private home, so only the settings files are owned."""
        plan = _gemini_plan(
            tmp_path,
            profile_config={
                "native_settings_scope": "workspace",
                "working_directory": str(tmp_path),
            },
            step_env={
                "GEMINI_CLI_HOME": "/elsewhere",
                "GEMINI_CLI_SYSTEM_SETTINGS_PATH": "/elsewhere/settings.json",
            },
        )

        assert plan.steps[0].env == {"GEMINI_CLI_HOME": "/elsewhere"}
        assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" in caplog.text

    def test_other_adapters_keep_gemini_variables(self, tmp_path: Path) -> None:
        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "defaults": {
                    "default_adapter": "claude-code-cli",
                    "adapters": {
                        "claude-code-cli": {"type": "claude-code-cli", "config": {}},
                    },
                },
                "steps": [
                    {
                        "id": "summarize",
                        "mode": "agent",
                        "prompt_prefix": "do thing",
                        "env": {"GEMINI_CLI_HOME": "/elsewhere"},
                        "outputs": {"summary": {"path": "summary.md", "kind": "file"}},
                    }
                ],
            }
        )
        process_path = tmp_path / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        plan = build_plan(spec, {}, process_path=process_path)

        assert plan.steps[0].env == {"GEMINI_CLI_HOME": "/elsewhere"}
