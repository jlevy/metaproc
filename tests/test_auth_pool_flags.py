"""Tests for AuthPoolFlags — the centralized auth-pool dispatch shape.

Phase 10 of the Vehicle A pool redesign (the original design, this regression).
Spec: docs/arch/arch-metaproc-core.md.

The dataclass is the single source of truth for the five-flag chain
(METAPROC_AUTH_ACCOUNT / _BACKEND / _FALLBACK_POLICY / _INCLUDE_LABELS /
_EXCLUDE_LABELS). Every consumer in the dispatch chain
(orchestrator_dispatch, orchestrator_entrypoint, worker_dispatch,
worker_entrypoint, run-process / run-parallel CLIs) goes through these
methods rather than hand-coding env-var names.

Coverage:
- ClassVar env-var names match the MetaprocEnv registry (catches drift).
- from_env / to_env_vars round-trip both directions, with and without
  optional fields.
- to_cli_flags encodes correctly, including CSV → repeated-flag
  expansion for include/exclude labels.
- Empty / whitespace / consecutive-comma input handling.
- is_pool_dispatch_enabled gates correctly on auth_account.
"""

from __future__ import annotations

import pytest

from metaproc.config.env_vars import MetaprocEnv
from metaproc.dispatch.auth_pool_flags import AuthPoolFlags


class TestEnvVarNamesAreRegistrySourced:
    def test_class_var_names_match_metaproc_env_members(self) -> None:
        # If someone renames a MetaprocEnv member without updating
        # AuthPoolFlags, this test catches it. The whole point of the
        # ClassVar-from-registry pattern is that this can't drift.
        assert AuthPoolFlags.ENV_AUTH_ACCOUNT == "METAPROC_AUTH_ACCOUNT"
        assert AuthPoolFlags.ENV_AUTH_BACKEND == "METAPROC_AUTH_BACKEND"
        assert AuthPoolFlags.ENV_AUTH_FALLBACK_POLICY == "METAPROC_AUTH_FALLBACK_POLICY"
        assert AuthPoolFlags.ENV_AUTH_INCLUDE_LABELS == "METAPROC_AUTH_INCLUDE_LABELS"
        assert AuthPoolFlags.ENV_AUTH_EXCLUDE_LABELS == "METAPROC_AUTH_EXCLUDE_LABELS"

    def test_class_var_names_match_metaproc_env_dot_name(self) -> None:
        # The lookup path that prevents drift: ClassVar values are
        # MetaprocEnv.X.name, so a registry rename automatically
        # propagates here.
        assert MetaprocEnv.METAPROC_AUTH_ACCOUNT.name == AuthPoolFlags.ENV_AUTH_ACCOUNT
        assert MetaprocEnv.METAPROC_AUTH_BACKEND.name == AuthPoolFlags.ENV_AUTH_BACKEND
        assert (
            MetaprocEnv.METAPROC_AUTH_FALLBACK_POLICY.name == AuthPoolFlags.ENV_AUTH_FALLBACK_POLICY
        )
        assert (
            MetaprocEnv.METAPROC_AUTH_INCLUDE_LABELS.name == AuthPoolFlags.ENV_AUTH_INCLUDE_LABELS
        )
        assert (
            MetaprocEnv.METAPROC_AUTH_EXCLUDE_LABELS.name == AuthPoolFlags.ENV_AUTH_EXCLUDE_LABELS
        )

    def test_all_env_var_names_returns_in_declaration_order(self) -> None:
        names = AuthPoolFlags.all_env_var_names()
        assert names == (
            "METAPROC_AUTH_ACCOUNT",
            "METAPROC_AUTH_BACKEND",
            "METAPROC_AUTH_FALLBACK_POLICY",
            "METAPROC_AUTH_POLICY",
            "METAPROC_AUTH_INCLUDE_LABELS",
            "METAPROC_AUTH_EXCLUDE_LABELS",
            "METAPROC_AUTH_CROSS_QUOTA_GROUP",
        )


class TestDefaults:
    def test_default_construction_is_empty(self) -> None:
        flags = AuthPoolFlags()
        assert flags.auth_account == ""
        assert flags.auth_backend == ""
        assert flags.auth_fallback_policy == ""
        # auth_policy defaults to "" meaning "use the default" — the
        # CLI surface picks ROUND_ROBIN for ≥ 2 included labels and
        # PRIORITY_ORDER otherwise (plan-2026-05-03).
        assert flags.auth_policy == ""
        assert flags.auth_include_labels == ()
        assert flags.auth_exclude_labels == ()
        # Cross-quota-group defaults to True (the spec-correct
        # behavior); operators opt out, not in.
        assert flags.auth_cross_quota_group is True

    def test_default_is_not_pool_dispatch_enabled(self) -> None:
        assert AuthPoolFlags().is_pool_dispatch_enabled() is False

    def test_auth_account_alone_enables_pool_dispatch(self) -> None:
        # auth_account is the master switch — once it's set, the layer
        # above the pool dispatcher constructs PoolDispatchConfig.
        assert AuthPoolFlags(auth_account="claude-code-cli").is_pool_dispatch_enabled() is True


class TestFromEnv:
    def test_empty_mapping_returns_default(self) -> None:
        flags = AuthPoolFlags.from_env({})
        assert flags == AuthPoolFlags()

    def test_full_payload(self) -> None:
        flags = AuthPoolFlags.from_env(
            {
                "METAPROC_AUTH_ACCOUNT": "claude-code-cli",
                "METAPROC_AUTH_BACKEND": "gcp-secret-manager",
                "METAPROC_AUTH_FALLBACK_POLICY": "same-provider",
                "METAPROC_AUTH_INCLUDE_LABELS": "alt1,alt2",
                "METAPROC_AUTH_EXCLUDE_LABELS": "laptop",
            }
        )
        assert flags.auth_account == "claude-code-cli"
        assert flags.auth_backend == "gcp-secret-manager"
        assert flags.auth_fallback_policy == "same-provider"
        assert flags.auth_include_labels == ("alt1", "alt2")
        assert flags.auth_exclude_labels == ("laptop",)

    def test_csv_with_whitespace_stripped(self) -> None:
        flags = AuthPoolFlags.from_env({"METAPROC_AUTH_INCLUDE_LABELS": " alt1 , alt2 ,, alt3 ,"})
        # Whitespace stripped, consecutive commas tolerated, trailing
        # comma ignored.
        assert flags.auth_include_labels == ("alt1", "alt2", "alt3")

    def test_partial_payload_missing_keys_default_empty(self) -> None:
        flags = AuthPoolFlags.from_env({"METAPROC_AUTH_ACCOUNT": "claude-code-cli"})
        assert flags.auth_account == "claude-code-cli"
        assert flags.auth_backend == ""
        assert flags.auth_include_labels == ()


class TestToEnvVars:
    def test_default_emits_empty_dict(self) -> None:
        # Empty fields are OMITTED — the receiver's from_env defaults
        # missing keys to empty, so a baseline call (no auth-pool
        # dispatch) emits no auth env vars at all.
        assert AuthPoolFlags().to_env_vars() == {}

    def test_full_payload(self) -> None:
        flags = AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_backend="gcp-secret-manager",
            auth_fallback_policy="same-provider",
            auth_include_labels=("alt1", "alt2"),
            auth_exclude_labels=("laptop",),
        )
        env = flags.to_env_vars()
        assert env == {
            "METAPROC_AUTH_ACCOUNT": "claude-code-cli",
            "METAPROC_AUTH_BACKEND": "gcp-secret-manager",
            "METAPROC_AUTH_FALLBACK_POLICY": "same-provider",
            "METAPROC_AUTH_INCLUDE_LABELS": "alt1,alt2",
            "METAPROC_AUTH_EXCLUDE_LABELS": "laptop",
        }

    def test_partial_payload_omits_unset_keys(self) -> None:
        flags = AuthPoolFlags(auth_account="claude-code-cli")
        env = flags.to_env_vars()
        # Only the set field appears; unset ones are absent (not "").
        assert env == {"METAPROC_AUTH_ACCOUNT": "claude-code-cli"}
        assert "METAPROC_AUTH_BACKEND" not in env
        assert "METAPROC_AUTH_INCLUDE_LABELS" not in env

    def test_empty_label_tuple_omits_csv(self) -> None:
        # Empty label tuple → no CSV key. Avoids encoding "" as a
        # round-trip artifact (which would survive parse as () but
        # produce confusing log lines).
        flags = AuthPoolFlags(auth_account="claude-code-cli", auth_include_labels=())
        assert "METAPROC_AUTH_INCLUDE_LABELS" not in flags.to_env_vars()


class TestToCliFlags:
    def test_default_emits_empty_argv(self) -> None:
        assert AuthPoolFlags().to_cli_flags() == []

    def test_full_payload(self) -> None:
        flags = AuthPoolFlags(
            auth_account="claude-code-cli",
            auth_backend="gcp-secret-manager",
            auth_fallback_policy="same-provider",
            auth_include_labels=("alt1", "alt2"),
            auth_exclude_labels=("laptop",),
        )
        cmd = flags.to_cli_flags()
        # Scalar flags appear once with their value; multi-value labels
        # expand to repeated --auth-X-labels <value> pairs.
        assert cmd == [
            "--auth-account",
            "claude-code-cli",
            "--auth-backend",
            "gcp-secret-manager",
            "--auth-fallback-policy",
            "same-provider",
            "--auth-include-labels",
            "alt1",
            "--auth-include-labels",
            "alt2",
            "--auth-exclude-labels",
            "laptop",
        ]

    def test_partial_payload_omits_unset_flags(self) -> None:
        flags = AuthPoolFlags(auth_account="claude-code-cli")
        cmd = flags.to_cli_flags()
        assert cmd == ["--auth-account", "claude-code-cli"]
        # No "--auth-backend ''" or similar empty pair.
        assert "--auth-backend" not in cmd

    def test_empty_label_tuple_emits_no_label_flags(self) -> None:
        flags = AuthPoolFlags(auth_account="claude-code-cli", auth_include_labels=())
        cmd = flags.to_cli_flags()
        assert "--auth-include-labels" not in cmd

    def test_auth_policy_round_robin_appears_in_cli(self) -> None:
        flags = AuthPoolFlags(auth_account="claude-code-cli", auth_policy="round-robin")
        cmd = flags.to_cli_flags()
        assert "--auth-policy" in cmd
        idx = cmd.index("--auth-policy")
        assert cmd[idx + 1] == "round-robin"

    def test_auth_policy_empty_omitted(self) -> None:
        # Default-empty auth_policy means "use the dispatcher's default
        # switching" — encoder must not write the flag with a literal
        # empty string (typer would reject it as invalid).
        flags = AuthPoolFlags(auth_account="claude-code-cli", auth_policy="")
        cmd = flags.to_cli_flags()
        assert "--auth-policy" not in cmd

    def test_auth_policy_appears_in_env_vars(self) -> None:
        flags = AuthPoolFlags(auth_account="claude-code-cli", auth_policy="least-active")
        env = flags.to_env_vars()
        assert env.get("METAPROC_AUTH_POLICY") == "least-active"


class TestRoundTrip:
    """Round-trip coverage — every shape that survives env-var transit must round-trip."""

    @pytest.mark.parametrize(
        "flags",
        [
            AuthPoolFlags(),
            AuthPoolFlags(auth_account="claude-code-cli"),
            AuthPoolFlags(auth_account="claude-code-cli", auth_include_labels=("alt1", "alt2")),
            AuthPoolFlags(
                auth_account="claude-code-cli",
                auth_backend="gcp-secret-manager",
                auth_fallback_policy="same-provider",
                auth_include_labels=("alt1", "alt2", "alt3"),
                auth_exclude_labels=("laptop",),
            ),
            AuthPoolFlags(
                auth_account="claude-code-cli",
                auth_cross_quota_group=False,
            ),
        ],
    )
    def test_env_round_trip_preserves_shape(self, flags: AuthPoolFlags) -> None:
        # Encode to env vars, decode back via from_env — must produce
        # an equal AuthPoolFlags. This is the contract the worker leg
        # depends on: orchestrator's encoded payload must arrive at
        # the worker bit-identical.
        env = flags.to_env_vars()
        round_tripped = AuthPoolFlags.from_env(env)
        assert round_tripped == flags


class TestCrossQuotaGroup:
    """auth_cross_quota_group flag — encode/decode + opt-out semantics."""

    def test_default_true_emits_no_env_var(self) -> None:
        # Default-true path is silent — operator opting in to a default
        # they're already on shouldn't add an env var to the dispatch
        # chain. Keeps existing dispatches unchanged when this field
        # was added.
        env = AuthPoolFlags(auth_account="claude-code-cli").to_env_vars()
        assert "METAPROC_AUTH_CROSS_QUOTA_GROUP" not in env

    def test_default_true_emits_no_cli_flag(self) -> None:
        cmd = AuthPoolFlags(auth_account="claude-code-cli").to_cli_flags()
        assert "--no-auth-cross-quota-group" not in cmd
        assert "--auth-cross-quota-group" not in cmd

    def test_opt_out_emits_env_var(self) -> None:
        env = AuthPoolFlags(
            auth_account="claude-code-cli", auth_cross_quota_group=False
        ).to_env_vars()
        assert env["METAPROC_AUTH_CROSS_QUOTA_GROUP"] == "false"

    def test_opt_out_emits_cli_flag(self) -> None:
        cmd = AuthPoolFlags(
            auth_account="claude-code-cli", auth_cross_quota_group=False
        ).to_cli_flags()
        assert "--no-auth-cross-quota-group" in cmd

    def test_from_env_unset_decodes_true(self) -> None:
        # Unset env reads as the default behavior. Mirrors how the
        # worker entrypoint constructs AuthPoolFlags from a Batch job
        # env that pre-dates the new flag — they get cross-quota-group
        # ON automatically, no migration needed.
        flags = AuthPoolFlags.from_env({"METAPROC_AUTH_ACCOUNT": "claude-code-cli"})
        assert flags.auth_cross_quota_group is True

    @pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "NO"])
    def test_from_env_falsy_decodes_false(self, raw: str) -> None:
        flags = AuthPoolFlags.from_env(
            {
                "METAPROC_AUTH_ACCOUNT": "claude-code-cli",
                "METAPROC_AUTH_CROSS_QUOTA_GROUP": raw,
            }
        )
        assert flags.auth_cross_quota_group is False

    @pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "anything-else"])
    def test_from_env_truthy_or_unrecognized_decodes_true(self, raw: str) -> None:
        # Non-falsy reads as True so a stray operator typo doesn't
        # silently disable cross-group expansion.
        flags = AuthPoolFlags.from_env(
            {
                "METAPROC_AUTH_ACCOUNT": "claude-code-cli",
                "METAPROC_AUTH_CROSS_QUOTA_GROUP": raw,
            }
        )
        assert flags.auth_cross_quota_group is True
