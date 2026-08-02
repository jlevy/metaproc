"""Planner-side adapter config validation regressions."""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import pytest
from pytest import LogCaptureFixture, MonkeyPatch

from metaproc.engine.build_plan import build_plan
from metaproc.models.authored import ProcessSpec


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip())


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
                    "id": "mine-adhoc",
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
    def test_harness_runtime_config_survives_adapter_validation(self) -> None:
        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "defaults": {
                    "default_adapter": "gemini-cli",
                    "adapters": {
                        "gemini-cli": {
                            "type": "gemini-cli",
                            "config": {
                                "model": "gemini-3.6-flash",
                                "accept_valid_outputs_on_timeout": True,
                                "capture_final_response_to": "result",
                            },
                        }
                    },
                },
                "steps": [
                    {
                        "id": "author",
                        "mode": "agent",
                        "prompt_prefix": "do thing",
                        "outputs": {"result": {"path": "result.md", "kind": "file"}},
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            process_path = Path(tmpdir) / "test.process.md"
            _write(process_path, "---\nprocess:\n  name: test\n---\n")
            plan = build_plan(spec, {}, process_path=process_path)

        assert plan.steps[0].adapter.config["accept_valid_outputs_on_timeout"] is True
        assert plan.steps[0].adapter.config["capture_final_response_to"] == "result"

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
                        "id": "mine-adhoc",
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
