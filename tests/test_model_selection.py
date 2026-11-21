"""Regression tests for adapter model selection at command construction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from metaproc.adapters.claude_cli import ClaudeCodeCliAdapter
from metaproc.adapters.codex_cli import CodexCliAdapter
from metaproc.adapters.gemini_cli import GeminiCliAdapter
from metaproc.adapters.pi_cli import PiCliAdapter
from metaproc.settings import (
    CLAUDE_DEFAULT_MODEL,
    CODEX_DEFAULT_MODEL,
    GEMINI_DEFAULT_MODEL,
    PI_DEFAULT_MODEL,
)

CommandBuilder = Callable[[Path, dict[str, object], dict[str, str]], list[str]]


def _selected_model(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


@pytest.mark.parametrize(
    ("model", "flag"),
    [
        ("claude-fable-5-2", "--model"),
        ("claude-opus-5-5", "--model"),
        ("claude-sonnet-5-5", "--model"),
        ("claude-haiku-5-5", "--model"),
        ("claude-fable-5-1", "--model"),
        ("claude-opus-5", "--model"),
        ("claude-sonnet-5", "--model"),
        ("claude-opus-4-8", "--model"),
    ],
)
def test_claude_preserves_accepted_explicit_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    flag: str,
) -> None:
    monkeypatch.setattr("metaproc.adapters.claude_cli._claude_version_drift", lambda: None)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Select the requested model.", encoding="utf-8")

    command = ClaudeCodeCliAdapter().build_command(
        prompt,
        {"permission_mode": "default", "model": model},
        {},
    )

    assert _selected_model(command, flag) == model


@pytest.mark.parametrize(
    "model",
    [
        "gpt-6.2-astra",
        "gpt-6.2-sol",
        "gpt-6.1-sol",
        "gpt-6.1-terra",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
    ],
)
def test_codex_preserves_accepted_explicit_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    monkeypatch.setattr("metaproc.adapters.codex_cli._codex_version_drift", lambda: None)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Select the requested model.", encoding="utf-8")

    command = CodexCliAdapter().build_command(
        prompt,
        {"permission_mode": "default", "model": model},
        {},
    )

    assert _selected_model(command, "-m") == model


@pytest.mark.parametrize(
    "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
)
def test_codex_accepts_pinned_cli_effort_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    monkeypatch.setattr("metaproc.adapters.codex_cli._codex_version_drift", lambda: None)
    adapter = CodexCliAdapter()
    config: dict[str, object] = {"permission_mode": "default", "effort": effort}
    assert adapter.validate_config(config) == []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Preserve effort syntax.", encoding="utf-8")
    assert f"model_reasoning_effort={effort}" in adapter.build_command(prompt, config, {})


@pytest.mark.parametrize(
    "model",
    [
        "gemini-4-carbon",
        "gemini-4-argon",
        "gemini-4-hydrogen",
        "gemini-4-neon",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview-customtools",
    ],
)
def test_gemini_preserves_accepted_explicit_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    monkeypatch.setattr("metaproc.adapters.gemini_cli._gemini_version_drift", lambda: None)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Select the requested model.", encoding="utf-8")

    adapter = GeminiCliAdapter()
    config: dict[str, object] = {"model": model}
    assert adapter.validate_config(config) == []
    command = adapter.build_command(prompt, config, {})

    assert _selected_model(command, "-m") == model


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("anthropic", "opus"),
        ("anthropic", "sonnet"),
        ("anthropic", "claude-fable-5-2"),
        ("anthropic", "claude-fable-5-1"),
        ("anthropic", "claude-opus-5-5"),
        ("anthropic", "claude-sonnet-5-5"),
        ("anthropic", "claude-haiku-5-5"),
        ("anthropic", "claude-opus-5"),
        ("anthropic", "claude-sonnet-5"),
        ("openai", "gpt-6.1-sol"),
        ("openai", "gpt-6-astra"),
        ("openai", "gpt-5.6-sol"),
        ("google-vertex", "gemini-4-argon"),
        ("google-vertex", "gemini-4-carbon"),
        ("google-vertex", "gemini-4-hydrogen"),
        ("google-vertex", "gemini-3.8-flash"),
        ("google-vertex", "gemini-3.5-flash-lite"),
        ("google-vertex", "gemini-3.1-pro-preview-customtools"),
    ],
)
def test_pi_preserves_accepted_explicit_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
) -> None:
    monkeypatch.setattr("metaproc.adapters.pi_cli._pi_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.pi_cli._resolve_pi_binary", lambda: "/usr/bin/pi")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Select the requested model.", encoding="utf-8")

    command = PiCliAdapter().build_command(
        prompt,
        {"provider": provider, "model": model},
        {},
    )

    assert _selected_model(command, "--model") == model


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("vertex-maas", "zai-org/glm-5-maas"),
        ("vertex-maas", "glm-5-maas"),
        ("google-vertex", "gemini-3.5-flash"),
        ("openai", "gpt-5.4-mini"),
    ],
)
def test_pi_rejects_retired_models(provider: str, model: str) -> None:
    rejections = PiCliAdapter().validate_config({"provider": provider, "model": model})
    assert any(rejection.key == "model" for rejection in rejections)


@pytest.mark.parametrize(
    ("adapter", "model"),
    [
        (CodexCliAdapter(), "gpt-5.4"),
        (CodexCliAdapter(), "gpt-5.4-mini"),
        (CodexCliAdapter(), "gpt-5.3-codex-spark"),
        (GeminiCliAdapter(), "gemini-3.5-flash"),
        (GeminiCliAdapter(), "gemini-3.7-flash"),
        (GeminiCliAdapter(), "gemini-3.1-flash-lite"),
        (GeminiCliAdapter(), "gemini-3-flash-preview"),
        (GeminiCliAdapter(), "gemini-3-pro-preview"),
        (GeminiCliAdapter(), "auto-gemini-3"),
    ],
)
def test_removed_models_are_rejected(
    adapter: CodexCliAdapter | GeminiCliAdapter, model: str
) -> None:
    config: dict[str, object] = {"permission_mode": "default", "model": model}
    if isinstance(adapter, GeminiCliAdapter):
        config = {"model": model}
    rejections = adapter.validate_config(config)
    assert any(rejection.key == "model" for rejection in rejections)


@pytest.mark.parametrize(
    ("builder", "config", "flag", "default"),
    [
        (
            ClaudeCodeCliAdapter().build_command,
            {"permission_mode": "default"},
            "--model",
            CLAUDE_DEFAULT_MODEL,
        ),
        (
            CodexCliAdapter().build_command,
            {"permission_mode": "default"},
            "-m",
            CODEX_DEFAULT_MODEL,
        ),
        (GeminiCliAdapter().build_command, {}, "-m", GEMINI_DEFAULT_MODEL),
        (PiCliAdapter().build_command, {}, "--model", PI_DEFAULT_MODEL),
    ],
)
def test_omitted_model_keeps_adapter_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    builder: CommandBuilder,
    config: dict[str, object],
    flag: str,
    default: str,
) -> None:
    monkeypatch.setattr("metaproc.adapters.claude_cli._claude_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.codex_cli._codex_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.gemini_cli._gemini_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.pi_cli._pi_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.pi_cli._resolve_pi_binary", lambda: "/usr/bin/pi")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Use the default model.", encoding="utf-8")

    command = builder(prompt, config, {})

    assert _selected_model(command, flag) == default


@pytest.mark.parametrize(
    ("builder", "config", "adapter_name"),
    [
        (
            ClaudeCodeCliAdapter().build_command,
            {"permission_mode": "default", "model": "unknown-model"},
            "claude-code-cli",
        ),
        (
            CodexCliAdapter().build_command,
            {"permission_mode": "default", "model": "unknown-model"},
            "codex-cli",
        ),
        (GeminiCliAdapter().build_command, {"model": "unknown-model"}, "gemini-cli"),
        (PiCliAdapter().build_command, {"model": "unknown-model"}, "pi-cli"),
    ],
)
@pytest.mark.parametrize("unknown_model", ["unknown-model", "", 0, False])
def test_unknown_explicit_model_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    builder: CommandBuilder,
    config: dict[str, object],
    adapter_name: str,
    unknown_model: object,
) -> None:
    monkeypatch.setattr("metaproc.adapters.claude_cli._claude_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.codex_cli._codex_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.gemini_cli._gemini_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.pi_cli._pi_version_drift", lambda: None)
    monkeypatch.setattr("metaproc.adapters.pi_cli._resolve_pi_binary", lambda: "/usr/bin/pi")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Reject the unknown model.", encoding="utf-8")

    with pytest.raises(ValueError, match=rf"unknown {adapter_name} model"):
        builder(prompt, config | {"model": unknown_model}, {})


@pytest.mark.parametrize(
    ("adapter", "config", "replacement"),
    [
        (GeminiCliAdapter(), {"model": "gemini-3.5-flash"}, "gemini-4-hydrogen"),
        (
            CodexCliAdapter(),
            {"permission_mode": "default", "model": "gpt-5.4"},
            "gpt-6.1-sol",
        ),
        (PiCliAdapter(), {"provider": "vertex-maas", "model": "glm-5-maas"}, "glm-5.2-maas"),
    ],
)
def test_removed_model_rejection_names_the_replacement(
    adapter: CodexCliAdapter | GeminiCliAdapter | PiCliAdapter,
    config: dict[str, object],
    replacement: str,
) -> None:
    reasons = [r.reason for r in adapter.validate_config(config) if r.key == "model"]
    assert reasons
    assert "removed from Metaproc's model catalog" in reasons[0]
    assert replacement in reasons[0]
