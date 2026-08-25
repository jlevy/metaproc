"""Tests for the orchestrator entrypoint command builder.

Covers the auth-pool flag passthrough that the cloud-dispatch path needs:
operators pass `--auth-*` flags to `run-process --cloud`, the orchestrator
forwards them as env vars, and the entrypoint must rebuild them onto the
inner `run-process` command. Without this, cloud workers silently fall back
to the legacy single-credential path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from metaproc.cloud.gcp.container_bootstrap import BootstrapResult
from metaproc.cloud.gcp.orchestrator_entrypoint import build_runprocess_cmd, main
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags, _split_csv


def _baseline_cmd(
    *,
    auth_account: str = "",
    auth_backend: str = "",
    auth_fallback_policy: str = "",
    auth_include_labels: str = "",
    auth_exclude_labels: str = "",
    **overrides: Any,
) -> list[str]:
    """Build a baseline run-process cmd; auth_* kwargs construct AuthPoolFlags.

    Phase 10: build_runprocess_cmd takes a single
    AuthPoolFlags argument. The test helpers preserve the ergonomics
    of CSV-string fields by going through the dataclass's encoding.
    """

    kwargs: dict[str, Any] = {
        "resolved_process_spec": "/work/repo/path/to/spec.process.md",
        "variables": {"RUN_ID": "test-run"},
    }
    if any(
        (auth_account, auth_backend, auth_fallback_policy, auth_include_labels, auth_exclude_labels)
    ):
        kwargs["auth_flags"] = AuthPoolFlags(
            auth_account=auth_account,
            auth_backend=auth_backend,
            auth_fallback_policy=auth_fallback_policy,
            auth_include_labels=_split_csv(auth_include_labels),
            auth_exclude_labels=_split_csv(auth_exclude_labels),
        )
    kwargs.update(overrides)
    return build_runprocess_cmd(**kwargs)


class TestBaseline:
    def test_minimal_command(self) -> None:
        cmd = _baseline_cmd()
        assert cmd[:7] == [
            "python",
            "-m",
            "metaproc",
            "run-process",
            "/work/repo/path/to/spec.process.md",
            "--backend",
            "gcp-worker",
        ]
        assert "--var" in cmd
        assert "RUN_ID=test-run" in cmd

    def test_no_cloud_flag(self) -> None:
        # --cloud must never appear: the orchestrator is already inside cloud.
        cmd = _baseline_cmd()
        assert "--cloud" not in cmd

    def test_omits_empty_optionals(self) -> None:
        cmd = _baseline_cmd()
        for flag in (
            "--num-workers",
            "--initial-concurrency",
            "--variant",
            "--auth-account",
            "--auth-backend",
            "--auth-include-labels",
            "--auth-exclude-labels",
            "--auth-fallback-policy",
            "--force",
            "--continue-on-error",
            "--no-spot",
        ):
            assert flag not in cmd, f"unexpected flag in baseline: {flag}"


class TestEntrypoint:
    def test_forwards_orchestrator_admission_marker_to_inner_process(self) -> None:
        env = {
            "METAPROC_PROCESS_SPEC": "path/to/spec.process.md",
            "METAPROC_VARS": '{"RUN_ID": "test-run"}',
            "METAPROC_GCP_ORCHESTRATOR": "1",
        }
        with (
            patch.dict("os.environ", env, clear=True),
            patch(
                "metaproc.cloud.gcp.orchestrator_entrypoint.bootstrap_container",
                return_value=BootstrapResult(work_dir="/work/repo"),
            ),
            patch("metaproc.cloud.gcp.orchestrator_entrypoint.log_resource_context"),
            patch("metaproc.cloud.gcp.orchestrator_entrypoint._run", return_value=0) as run,
        ):
            assert main() == 0

        child_env = run.call_args.kwargs["env"]
        assert child_env["METAPROC_GCP_ORCHESTRATOR"] == "1"


class TestAuthFlags:
    def test_initial_concurrency_flag_propagates(self) -> None:
        cmd = _baseline_cmd(initial_concurrency="3")
        assert "--initial-concurrency" in cmd
        assert cmd[cmd.index("--initial-concurrency") + 1] == "3"

    def test_auth_account_flag_propagates(self) -> None:
        cmd = _baseline_cmd(auth_account="claude-code-cli")
        assert "--auth-account" in cmd
        assert cmd[cmd.index("--auth-account") + 1] == "claude-code-cli"

    def test_auth_backend_flag_propagates(self) -> None:
        cmd = _baseline_cmd(auth_backend="gcp-secret-manager")
        assert "--auth-backend" in cmd
        assert cmd[cmd.index("--auth-backend") + 1] == "gcp-secret-manager"

    def test_auth_fallback_policy_flag_propagates(self) -> None:
        cmd = _baseline_cmd(auth_fallback_policy="same-provider")
        assert "--auth-fallback-policy" in cmd
        assert cmd[cmd.index("--auth-fallback-policy") + 1] == "same-provider"

    def test_include_labels_repeated(self) -> None:
        cmd = _baseline_cmd(auth_include_labels="alt1,alt2,alt3")
        flag_positions = [i for i, x in enumerate(cmd) if x == "--auth-include-labels"]
        assert len(flag_positions) == 3
        values = [cmd[i + 1] for i in flag_positions]
        assert values == ["alt1", "alt2", "alt3"]

    def test_include_labels_strips_whitespace(self) -> None:
        cmd = _baseline_cmd(auth_include_labels="alt1, alt2 , alt3")
        values = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--auth-include-labels"]
        assert values == ["alt1", "alt2", "alt3"]

    def test_include_labels_empty_csv_skipped(self) -> None:
        cmd = _baseline_cmd(auth_include_labels="alt1,,alt2")
        values = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--auth-include-labels"]
        assert values == ["alt1", "alt2"]

    def test_exclude_labels_repeated(self) -> None:
        cmd = _baseline_cmd(auth_exclude_labels="bad1,bad2")
        flag_positions = [i for i, x in enumerate(cmd) if x == "--auth-exclude-labels"]
        assert len(flag_positions) == 2
        values = [cmd[i + 1] for i in flag_positions]
        assert values == ["bad1", "bad2"]

    def test_full_auth_dispatch(self) -> None:
        # End-to-end shape an operator would use:
        # `run-process --cloud --auth-account claude-code-cli
        #  --auth-backend gcp-secret-manager --auth-fallback-policy same-provider
        #  --auth-include-labels alt1 --auth-include-labels alt2`.
        cmd = _baseline_cmd(
            auth_account="claude-code-cli",
            auth_backend="gcp-secret-manager",
            auth_fallback_policy="same-provider",
            auth_include_labels="alt1,alt2",
        )
        # All four pieces must land on the inner command, not just the first one.
        assert "--auth-account" in cmd
        assert "--auth-backend" in cmd
        assert "--auth-fallback-policy" in cmd
        assert cmd.count("--auth-include-labels") == 2
        # Mutually-exclusive guard: include-only path leaves exclude absent.
        assert "--auth-exclude-labels" not in cmd
