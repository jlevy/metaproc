"""Tests for the worker entrypoint command builder.

Covers the auth-pool flag passthrough on the **worker leg** of cloud
multi-account dispatch (Phase 6, closes the gap).
The orchestrator-leg is covered by ``test_orchestrator_entrypoint``.

The full chain:

    operator  →  `run-process --cloud --auth-*`
              ↓  OrchestratorDispatchConfig env vars
    orchestrator entrypoint  →  inner `run-process --backend gcp-worker --auth-*`
                              ↓  worker_dispatch propagates METAPROC_AUTH_*
    worker entrypoint  →  inner `run-parallel --backend local --auth-*`

Without this builder propagating ``--auth-*`` onto the inner
``run-parallel`` command, the worker would silently fall back to the
legacy single-credential bootstrap path even when the operator
explicitly opted into the pool.
"""

from __future__ import annotations

import os
from typing import Any

from metaproc.cloud.gcp.worker_entrypoint import (
    arm_legacy_bootstrap_guard,
    build_runparallel_cmd,
)
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags, _split_csv


def _baseline_cmd(**overrides: Any) -> list[str]:
    kwargs: dict[str, Any] = {
        "resolved_process_spec": "/work/repo/path/to/spec.process.md",
        "step": "predict-ticker",
        "items": "AAPL,MSFT,GOOG",
        "variables": {"RUN_ID": "test-run"},
    }
    kwargs.update(overrides)
    return build_runparallel_cmd(**kwargs)


def _auth(
    *,
    auth_account: str = "",
    auth_backend: str = "",
    auth_fallback_policy: str = "",
    auth_include_labels: str = "",
    auth_exclude_labels: str = "",
) -> AuthPoolFlags:
    """Build an AuthPoolFlags from the CSV/string shape the tests use.

    Mirrors how worker_entrypoint constructs flags from env vars: CSV
    strings split into tuples. Tests originally wrote the five fields
    as strings; this helper preserves that ergonomics on top of the
    new typed shape.
    """

    return AuthPoolFlags(
        auth_account=auth_account,
        auth_backend=auth_backend,
        auth_fallback_policy=auth_fallback_policy,
        auth_include_labels=_split_csv(auth_include_labels),
        auth_exclude_labels=_split_csv(auth_exclude_labels),
    )


class TestBaseline:
    def test_minimal_command(self) -> None:
        cmd = _baseline_cmd()
        assert cmd[:11] == [
            "python",
            "-m",
            "metaproc",
            "run-parallel",
            "/work/repo/path/to/spec.process.md",
            "--step",
            "predict-ticker",
            "--items",
            "AAPL,MSFT,GOOG",
            "--backend",
            "local",
        ]
        # --var is always present because RUN_ID is in baseline variables.
        assert "--var" in cmd
        assert "RUN_ID=test-run" in cmd

    def test_omits_empty_optionals(self) -> None:
        cmd = _baseline_cmd()
        for flag in (
            "--max-concurrency",
            "--initial-concurrency",
            "--variant",
            "--max-retries",
            "--adapter-config",
            "--auth-account",
            "--auth-backend",
            "--auth-fallback-policy",
            "--auth-include-labels",
            "--auth-exclude-labels",
        ):
            assert flag not in cmd, f"unexpected flag in baseline: {flag}"

    def test_multiple_variables(self) -> None:
        cmd = _baseline_cmd(variables={"RUN_ID": "r1", "EARNINGS_DATE": "2026-04-28"})
        # Each KEY=VALUE pair becomes its own --var entry.
        var_pairs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--var"]
        assert "RUN_ID=r1" in var_pairs
        assert "EARNINGS_DATE=2026-04-28" in var_pairs


class TestPlumbingFlags:
    def test_max_concurrency(self) -> None:
        cmd = _baseline_cmd(max_concurrency="25")
        assert "--max-concurrency" in cmd
        assert cmd[cmd.index("--max-concurrency") + 1] == "25"

    def test_initial_concurrency(self) -> None:
        cmd = _baseline_cmd(initial_concurrency="3")
        assert "--initial-concurrency" in cmd
        assert cmd[cmd.index("--initial-concurrency") + 1] == "3"

    def test_variant(self) -> None:
        cmd = _baseline_cmd(variant="claude-code-cli")
        assert "--variant" in cmd
        assert cmd[cmd.index("--variant") + 1] == "claude-code-cli"

    def test_max_retries(self) -> None:
        cmd = _baseline_cmd(max_retries="5")
        assert "--max-retries" in cmd
        assert cmd[cmd.index("--max-retries") + 1] == "5"

    def test_adapter_config_json_expanded_to_repeated_flags(self) -> None:
        cmd = _baseline_cmd(adapter_config_json='{"model": "claude-opus-4-7", "effort": "high"}')
        configs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--adapter-config"]
        assert "model=claude-opus-4-7" in configs
        assert "effort=high" in configs


class TestAuthPoolFlags:
    """Phase 6 — the worker-leg of the auth-pool flag chain."""

    def test_auth_account_alone_emits_account_flag(self) -> None:
        cmd = _baseline_cmd(auth_flags=_auth(auth_account="claude-code-cli"))
        assert "--auth-account" in cmd
        assert cmd[cmd.index("--auth-account") + 1] == "claude-code-cli"

    def test_auth_backend(self) -> None:
        cmd = _baseline_cmd(
            auth_flags=_auth(auth_account="claude-code-cli", auth_backend="gcp-secret-manager")
        )
        assert "--auth-backend" in cmd
        assert cmd[cmd.index("--auth-backend") + 1] == "gcp-secret-manager"

    def test_auth_fallback_policy(self) -> None:
        cmd = _baseline_cmd(
            auth_flags=_auth(auth_account="claude-code-cli", auth_fallback_policy="same-provider")
        )
        assert "--auth-fallback-policy" in cmd
        assert cmd[cmd.index("--auth-fallback-policy") + 1] == "same-provider"

    def test_auth_include_labels_csv_expands_to_repeated_flags(self) -> None:
        cmd = _baseline_cmd(
            auth_flags=_auth(auth_account="claude-code-cli", auth_include_labels="alt1,alt2,alt3")
        )
        labels = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--auth-include-labels"]
        assert labels == ["alt1", "alt2", "alt3"]

    def test_auth_include_labels_strips_whitespace_and_skips_empty(self) -> None:
        # CSV from env vars can have stray whitespace if a human edited
        # the dispatch payload. Survive that gracefully without
        # emitting an empty `--auth-include-labels ""` arg.
        cmd = _baseline_cmd(
            auth_flags=_auth(auth_account="claude-code-cli", auth_include_labels=" alt1 ,, alt2 ,")
        )
        labels = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--auth-include-labels"]
        assert labels == ["alt1", "alt2"]

    def test_auth_exclude_labels_csv_expands(self) -> None:
        cmd = _baseline_cmd(
            auth_flags=_auth(
                auth_account="claude-code-cli", auth_exclude_labels="laptop,deprecated"
            )
        )
        labels = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--auth-exclude-labels"]
        assert labels == ["laptop", "deprecated"]

    def test_full_pool_dispatch_combination(self) -> None:
        # The shape an operator gets after `run-process --cloud --auth-account
        # claude-code-cli --auth-include-labels alt1 --auth-include-labels alt2
        # --auth-fallback-policy same-provider --auth-backend gcp-secret-manager`.
        cmd = _baseline_cmd(
            auth_flags=_auth(
                auth_account="claude-code-cli",
                auth_backend="gcp-secret-manager",
                auth_fallback_policy="same-provider",
                auth_include_labels="alt1,alt2",
            )
        )
        # Every flag the orchestrator received should make it onto the
        # inner run-parallel command. Without this, cloud workers
        # silently fall back to the legacy single-credential bootstrap.
        assert "--auth-account" in cmd
        assert "--auth-backend" in cmd
        assert "--auth-fallback-policy" in cmd
        labels = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--auth-include-labels"]
        assert labels == ["alt1", "alt2"]


class TestEmptyAuthMeansLegacyPath:
    def test_empty_string_auth_inputs_treated_as_omitted(self) -> None:
        # Empty AuthPoolFlags fields stay empty in to_cli_flags; no
        # --auth-* args are emitted. Otherwise a worker would get
        # `--auth-account ""` and crash at parse time.
        cmd = _baseline_cmd(auth_flags=AuthPoolFlags())
        for flag in (
            "--auth-account",
            "--auth-backend",
            "--auth-fallback-policy",
            "--auth-include-labels",
            "--auth-exclude-labels",
        ):
            assert flag not in cmd

    def test_no_auth_flags_kwarg_at_all_emits_legacy_baseline(self) -> None:
        # The default: when the orchestrator was launched without
        # --auth-* flags, the worker entrypoint constructs an empty
        # AuthPoolFlags (or passes None) and no --auth-* flags appear
        # on the inner run-parallel command. Worker falls back to the
        # legacy single-credential bootstrap.
        cmd = _baseline_cmd()
        for flag in (
            "--auth-account",
            "--auth-backend",
            "--auth-fallback-policy",
            "--auth-include-labels",
            "--auth-exclude-labels",
        ):
            assert flag not in cmd


class TestLegacyBootstrapGuard:
    """Regression coverage: METAPROC_AUTH_POOL_RUN gate before adapter bootstrap.

    When auth-pool dispatch is enabled, the worker entrypoint must set
    METAPROC_AUTH_POOL_RUN=1 BEFORE the per-adapter ``bootstrap(home)``
    loop so each adapter's existing pool-aware no-op fires. Without
    this, ``CLAUDE_CODE_CREDS_JSON`` (if propagated) materializes
    ``~/.claude/.credentials.json`` at worker entry and competes with
    per-slot Vehicle A/B scoping at item launch. Legacy no-auth
    dispatches must NOT set the env var so the single-credential
    bootstrap path stays intact.
    """

    def test_pool_dispatch_enabled_arms_guard(self, monkeypatch) -> None:
        monkeypatch.delenv("METAPROC_AUTH_POOL_RUN", raising=False)
        flags = AuthPoolFlags(auth_account="claude-code-cli")
        arm_legacy_bootstrap_guard(flags)

        assert os.environ.get("METAPROC_AUTH_POOL_RUN") == "1"

    def test_no_auth_account_leaves_guard_unset(self, monkeypatch) -> None:
        # Legacy single-credential dispatch (no --auth-account) must
        # NOT set METAPROC_AUTH_POOL_RUN — adapter.bootstrap(home)
        # needs to run normally and materialize CLAUDE_CODE_CREDS_JSON.
        monkeypatch.delenv("METAPROC_AUTH_POOL_RUN", raising=False)
        flags = AuthPoolFlags()
        arm_legacy_bootstrap_guard(flags)

        assert "METAPROC_AUTH_POOL_RUN" not in os.environ

    def test_arms_guard_independent_of_other_auth_fields(self, monkeypatch) -> None:
        # is_pool_dispatch_enabled() keys on auth_account alone. Other
        # fields without an account are inert and must not arm the
        # guard (matches AuthPoolFlags's master-switch contract).
        monkeypatch.delenv("METAPROC_AUTH_POOL_RUN", raising=False)
        flags = AuthPoolFlags(
            auth_backend="gcp-secret-manager",
            auth_include_labels=("alt1",),
        )
        arm_legacy_bootstrap_guard(flags)

        assert "METAPROC_AUTH_POOL_RUN" not in os.environ
