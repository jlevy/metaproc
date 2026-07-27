"""Tests for the dispatch-start ambient-auth env sweep.

plan-2026-05-03 § Phase 5 this regression.

The per-item ``compose_slot_env`` check only fires when the first
matching-adapter item tries to launch — a long pool-bypass step can
complete before any pooled step's first item ever calls
``compose_slot_env``, so a stray ``ANTHROPIC_API_KEY`` costs the
duration of every preceding non-pool step. ``check_dispatch_auth_env``
fires once at dispatch start, before step 0.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.dispatch.preflight import (
    DispatchAuthEnvVerdict,
    check_dispatch_auth_env,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SYNTHETIC_PROCESS_PATH = str(_REPO_ROOT / "tests" / "fixtures" / "auth_env" / "mixed.process.md")


class TestCheckDispatchAuthEnvCleanEnv:
    """Clean env → go regardless of posture."""

    def test_clean_env_warn_returns_go(self):
        verdict = check_dispatch_auth_env("claude-code-cli", posture="warn", env={})
        assert verdict.status == "go"
        assert verdict.conflicting_keys == ()
        assert "clean" in verdict.message

    def test_clean_env_refuse_returns_go(self):
        verdict = check_dispatch_auth_env("claude-code-cli", posture="refuse", env={})
        assert verdict.status == "go"
        assert verdict.conflicting_keys == ()

    def test_unrelated_env_var_does_not_trigger(self):
        # OPENAI_API_KEY is the codex-cli refusal key; claude-code-cli
        # should not refuse on it.
        verdict = check_dispatch_auth_env(
            "claude-code-cli", posture="refuse", env={"OPENAI_API_KEY": "sk-test"}
        )
        assert verdict.status == "go"
        assert verdict.conflicting_keys == ()


class TestCheckDispatchAuthEnvConflict:
    """Conflict present → behavior gated by posture."""

    def test_anthropic_key_under_refuse_returns_refuse(self):
        verdict = check_dispatch_auth_env(
            "claude-code-cli",
            posture="refuse",
            env={"ANTHROPIC_API_KEY": "sk-ant-test"},
        )
        assert verdict.status == "refuse"
        assert verdict.conflicting_keys == ("ANTHROPIC_API_KEY",)
        # Operator-facing message must name the unset command.
        assert "unset ANTHROPIC_API_KEY" in verdict.message
        assert "env -u ANTHROPIC_API_KEY" in verdict.message

    def test_anthropic_key_under_warn_returns_warn(self):
        verdict = check_dispatch_auth_env(
            "claude-code-cli",
            posture="warn",
            env={"ANTHROPIC_API_KEY": "sk-ant-test"},
        )
        assert verdict.status == "warn"
        assert verdict.conflicting_keys == ("ANTHROPIC_API_KEY",)
        assert "ANTHROPIC_API_KEY" in verdict.message

    def test_openai_key_for_codex_adapter_returns_refuse(self):
        # Codex's refusal key is OPENAI_API_KEY — verifies the adapter
        # dispatch covers more than just claude-code-cli.
        verdict = check_dispatch_auth_env(
            "codex-cli",
            posture="refuse",
            env={"OPENAI_API_KEY": "sk-openai-test"},
        )
        assert verdict.status == "refuse"
        assert verdict.conflicting_keys == ("OPENAI_API_KEY",)
        assert "OPENAI_API_KEY" in verdict.message


class TestCheckDispatchAuthEnvOff:
    """Posture=off short-circuits without inspecting env."""

    def test_off_with_conflict_still_returns_go(self):
        verdict = check_dispatch_auth_env(
            "claude-code-cli",
            posture="off",
            env={"ANTHROPIC_API_KEY": "sk-ant-test"},
        )
        assert verdict.status == "go"
        assert verdict.conflicting_keys == ()
        assert "off" in verdict.message


class TestCheckDispatchAuthEnvEmptyKey:
    """Empty-string values are not a conflict (matches compose_slot_env)."""

    def test_empty_string_anthropic_key_does_not_refuse(self):
        # parent_env.get(k) is falsy on "", matching the existing
        # compose_slot_env behavior at pool_dispatch.py:208.
        verdict = check_dispatch_auth_env(
            "claude-code-cli",
            posture="refuse",
            env={"ANTHROPIC_API_KEY": ""},
        )
        assert verdict.status == "go"
        assert verdict.conflicting_keys == ()


class TestCheckDispatchAuthEnvUnknownAdapter:
    """Adapter with no refusal keys → always go."""

    def test_unknown_adapter_returns_go(self):
        # auth_override_refusal_keys returns () for adapters it doesn't
        # know about (pi-cli, gemini-cli, etc.). Those adapters bypass
        # the auth pool entirely so the env sweep is a no-op for them.
        verdict = check_dispatch_auth_env(
            "pi-cli",
            posture="refuse",
            env={"ANTHROPIC_API_KEY": "sk-ant-test"},
        )
        assert verdict.status == "go"
        assert verdict.conflicting_keys == ()


class TestRunProcessIntegration:
    """End-to-end: run-process honors the sweep before any DAG work."""

    def _invoke(self, env_overrides, *cli_args):
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

    def test_refuse_blocks_dry_run_when_anthropic_key_set(self):
        result = self._invoke(
            {"ANTHROPIC_API_KEY": "sk-ant-test"},
            "run-process",
            _SYNTHETIC_PROCESS_PATH,
            "--var",
            "RUNS_DIR=/tmp/metaproc-test-runs",
            "--var",
            "RUN_ID=test-env-refuse-integration",
            "--auth-account",
            "claude-code-cli",
            "--auth-preflight-quota-guard",
            "refuse",
            "--dry-run",
        )
        assert result.exit_code != 0
        # CLIError carries the operator-facing message; surfaces via
        # result.exception under Typer's error handler.
        assert result.exception is not None
        assert "ANTHROPIC_API_KEY" in str(result.exception)
        assert "unset" in str(result.exception)

    def test_warn_does_not_block_dry_run(self):
        result = self._invoke(
            {"ANTHROPIC_API_KEY": "sk-ant-test"},
            "run-process",
            _SYNTHETIC_PROCESS_PATH,
            "--var",
            "RUNS_DIR=/tmp/metaproc-test-runs",
            "--var",
            "RUN_ID=test-env-warn-integration",
            "--auth-account",
            "claude-code-cli",
            "--auth-preflight-quota-guard",
            "warn",
            "--dry-run",
        )
        # Warn surfaces in the log/output but does not raise.
        assert result.exit_code == 0, result.output[-500:]

    def test_clean_env_proceeds(self):
        result = self._invoke(
            {"ANTHROPIC_API_KEY": None},
            "run-process",
            _SYNTHETIC_PROCESS_PATH,
            "--var",
            "RUNS_DIR=/tmp/metaproc-test-runs",
            "--var",
            "RUN_ID=test-env-clean-integration",
            "--auth-account",
            "claude-code-cli",
            "--auth-preflight-quota-guard",
            "refuse",
            "--dry-run",
        )
        assert result.exit_code == 0, result.output[-500:]


class TestDispatchAuthEnvVerdictShape:
    """Verdict dataclass is frozen and tuple-safe."""

    def test_verdict_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(DispatchAuthEnvVerdict)
        # __dataclass_params__ exposes whether frozen=True was set.
        params = getattr(DispatchAuthEnvVerdict, "__dataclass_params__", None)
        assert params is not None
        assert params.frozen is True
