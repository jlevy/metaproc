"""Tests for `metaproc auth env <process>` subcommand.

plan-2026-05-03 § Phase 5 bead internal-reference.

Promotes the dispatch-start ambient-auth env sweep (bead internal-reference)
to a standalone operator tool. Loads a process spec, identifies the
adapter set, and reports per-adapter env-conflict verdicts so an
operator can diagnose before launching.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from metaproc.cli import app

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PI_PROCESS = str(_REPO_ROOT / "tests" / "fixtures" / "auth_env" / "pi-only.process.md")
_MIXED_PROCESS = str(_REPO_ROOT / "tests" / "fixtures" / "auth_env" / "mixed.process.md")


def _invoke(env_overrides, *cli_args):
    original_env = {k: os.environ.get(k) for k in env_overrides}
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        runner = CliRunner()
        return runner.invoke(app, list(cli_args))
    finally:
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestAuthEnvCleanShell:
    """Clean env → go regardless of process."""

    def test_pi_cli_process_clean_env(self):
        # The synthetic PI process uses pi-cli — auth_override_refusal_keys returns ()
        # for pi-cli so the verdict is always go even with refusal keys set.
        result = _invoke({"ANTHROPIC_API_KEY": None}, "auth", "env", _PI_PROCESS)
        assert result.exit_code == 0, result.output
        assert "overall: go" in result.output
        assert "adapter=pi-cli" in result.output

    def test_claude_process_clean_env(self):
        # The mixed fixture uses claude-code-cli + pi-cli — clean env → go.
        result = _invoke({"ANTHROPIC_API_KEY": None}, "auth", "env", _MIXED_PROCESS)
        assert result.exit_code == 0, result.output
        assert "overall: go" in result.output
        assert "adapter=claude-code-cli" in result.output
        assert "adapter=pi-cli" in result.output


class TestAuthEnvDirtyShell:
    """ANTHROPIC_API_KEY set → refuse for claude steps, go for pi-cli."""

    def test_refuse_when_anthropic_key_set_against_claude_process(self):
        result = _invoke(
            {"ANTHROPIC_API_KEY": "sk-test"},
            "auth",
            "env",
            _MIXED_PROCESS,
        )
        assert result.exit_code == 1, result.output
        assert "overall: refuse" in result.output
        # claude-code-cli adapter refuses; pi-cli adapter goes (no
        # refusal keys configured for it).
        assert "verdict: refuse" in result.output
        assert "verdict: go" in result.output  # pi-cli still goes
        assert "ANTHROPIC_API_KEY" in result.output
        assert "unset ANTHROPIC_API_KEY" in result.output

    def test_warn_does_not_set_nonzero_exit(self):
        result = _invoke(
            {"ANTHROPIC_API_KEY": "sk-test"},
            "auth",
            "env",
            _MIXED_PROCESS,
            "--posture",
            "warn",
        )
        assert result.exit_code == 0, result.output
        assert "overall: warn" in result.output
        assert "verdict: warn" in result.output

    def test_off_skips_check(self):
        result = _invoke(
            {"ANTHROPIC_API_KEY": "sk-test"},
            "auth",
            "env",
            _MIXED_PROCESS,
            "--posture",
            "off",
        )
        assert result.exit_code == 0, result.output
        assert "overall: go" in result.output


class TestAuthEnvJsonOutput:
    """--format json returns a structured report."""

    def test_json_shape_on_refuse(self):
        result = _invoke(
            {"ANTHROPIC_API_KEY": "sk-test"},
            "auth",
            "env",
            _MIXED_PROCESS,
            "--format",
            "json",
        )
        assert result.exit_code == 1, result.output
        report = json.loads(result.output)
        assert report["overall"] == "refuse"
        assert report["posture"] == "refuse"
        adapters = report["adapters"]
        assert "claude-code-cli" in adapters
        claude_section = adapters["claude-code-cli"]
        assert claude_section["verdict"] == "refuse"
        assert claude_section["conflicting_keys"] == ["ANTHROPIC_API_KEY"]
        # step_ids must list the actual steps that will use this adapter.
        assert "synthetic-claude-step" in claude_section["step_ids"]

    def test_json_shape_on_clean_env(self):
        result = _invoke(
            {"ANTHROPIC_API_KEY": None},
            "auth",
            "env",
            _MIXED_PROCESS,
            "--format",
            "json",
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["overall"] == "go"
        for section in report["adapters"].values():
            assert section["verdict"] == "go"
            assert section["conflicting_keys"] == []


class TestAuthEnvBadFlags:
    def test_invalid_posture_rejects(self):
        result = _invoke({}, "auth", "env", _PI_PROCESS, "--posture", "bogus")
        assert result.exit_code != 0

    def test_invalid_format_rejects(self):
        result = _invoke({}, "auth", "env", _PI_PROCESS, "--format", "yaml-not-yet")
        assert result.exit_code != 0
