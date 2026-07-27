"""Single source of truth for the METAPROC_AUTH_* / ``--auth-*`` chain.

The auth-pool dispatch chain propagates five flags through three layers:

.. code-block:: text

    operator CLI                ->  run-process / run-parallel
                                |   (OrchestratorDispatchConfig)
    orchestrator entrypoint     ->  inner `run-process --backend gcp-worker --auth-*`
                                |   (worker_dispatch env-var forwarding)
    worker entrypoint           ->  inner `run-parallel --backend local --auth-*`

Each of those sites used to hardcode the same five env-var names and CLI
flag names as bare strings, re-implementing CSV-to-tuple encoding for the
include/exclude labels independently, and silently breaking the chain on
any typo.

This module gives every layer one shape to import:

- :class:`AuthPoolFlags`: frozen dataclass holding the five values.
- :meth:`AuthPoolFlags.from_env`: read from process env (or test mapping).
- :meth:`AuthPoolFlags.to_env_vars`: encode for Batch job env_vars dict.
- :meth:`AuthPoolFlags.to_cli_flags`: encode as ``--auth-*`` argv list.
- :attr:`AuthPoolFlags.ENV_*` ClassVars: canonical names sourced from
  :class:`metaproc.config.env_vars.MetaprocEnv` so renames in the
  registry propagate everywhere.

Use ``AuthPoolFlags`` in every layer; never hand-construct or hand-parse
the env-var names.

The same pattern can be applied to other env-var cohorts that travel
together (secret refs, worker-dispatch payload, orchestrator-dispatch
payload, repo/git).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from metaproc.config.env_vars import MetaprocEnv


def _split_csv(raw: str) -> tuple[str, ...]:
    """Parse a CSV env-var value into a tuple of stripped non-empty entries.

    Tolerates stray whitespace and consecutive commas — operator-edited
    dispatch payloads sometimes have those, and silently surviving them
    is preferable to producing an empty ``--auth-include-labels ""`` arg
    that crashes the worker at parse time.
    """
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class AuthPoolFlags:
    """Operator-facing auth-pool dispatch configuration.

    Every layer in the dispatch chain constructs / consumes this
    instead of hand-coding the env-var or CLI flag names. Empty-string
    fields and empty-tuple labels mean "flag not set" — encoders omit
    them so a baseline call (no auth-pool dispatch) emits no auth env
    vars and no auth CLI args.
    """

    auth_account: str = ""
    auth_backend: str = ""
    auth_fallback_policy: str = ""
    # plan-2026-05-03: label selection policy. Empty
    # string means "use the default" — round-robin for ≥ 2 included
    # labels, else priority-order. Values: priority-order|round-robin|
    # least-active.
    auth_policy: str = ""
    auth_include_labels: tuple[str, ...] = ()
    auth_exclude_labels: tuple[str, ...] = ()
    # ``auth_cross_quota_group`` defaults to True (the spec-correct
    # behavior). The encoder only writes this field to env / CLI when
    # the operator explicitly opts out (``False``) — this keeps
    # baseline dispatches free of the new env var, and tests asserting
    # "no auth-pool env vars set" continue to hold.
    auth_cross_quota_group: bool = True

    # ── Canonical env-var names (sourced from MetaprocEnv registry) ──
    #
    # Using ``.name`` here means a typo in any of these turns into an
    # ``AttributeError`` at import time (caught by basedpyright /
    # static-analysis tools), not a silent runtime drift.
    ENV_AUTH_ACCOUNT: ClassVar[str] = MetaprocEnv.METAPROC_AUTH_ACCOUNT.name
    ENV_AUTH_BACKEND: ClassVar[str] = MetaprocEnv.METAPROC_AUTH_BACKEND.name
    ENV_AUTH_FALLBACK_POLICY: ClassVar[str] = MetaprocEnv.METAPROC_AUTH_FALLBACK_POLICY.name
    ENV_AUTH_POLICY: ClassVar[str] = MetaprocEnv.METAPROC_AUTH_POLICY.name
    ENV_AUTH_INCLUDE_LABELS: ClassVar[str] = MetaprocEnv.METAPROC_AUTH_INCLUDE_LABELS.name
    ENV_AUTH_EXCLUDE_LABELS: ClassVar[str] = MetaprocEnv.METAPROC_AUTH_EXCLUDE_LABELS.name
    ENV_AUTH_CROSS_QUOTA_GROUP: ClassVar[str] = MetaprocEnv.METAPROC_AUTH_CROSS_QUOTA_GROUP.name

    @classmethod
    def all_env_var_names(cls) -> tuple[str, ...]:
        """Return all auth-pool env-var names in declaration order.

        Useful for validation tools that want to assert no rogue
        auth-pool string lives outside this module.
        """
        return (
            cls.ENV_AUTH_ACCOUNT,
            cls.ENV_AUTH_BACKEND,
            cls.ENV_AUTH_FALLBACK_POLICY,
            cls.ENV_AUTH_POLICY,
            cls.ENV_AUTH_INCLUDE_LABELS,
            cls.ENV_AUTH_EXCLUDE_LABELS,
            cls.ENV_AUTH_CROSS_QUOTA_GROUP,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AuthPoolFlags:
        """Construct from process env (defaults to ``os.environ``).

        Missing env vars decode to empty fields — tests can pass an
        explicit empty mapping for "no auth-pool dispatch configured".
        Tests can also pass a synthetic mapping to drive
        :meth:`from_env` deterministically without ``monkeypatch``.
        """

        e: Mapping[str, str] = env if env is not None else os.environ
        # Cross-quota-group decoding: the env var is only set when the
        # operator opts out (``"false"``). Anything else (missing,
        # ``"true"``, ``"1"``, ``""``) reads as the spec-correct
        # default (True). Matches ``to_env_vars`` which only writes the
        # var when the field is False.
        cqg_raw = e.get(cls.ENV_AUTH_CROSS_QUOTA_GROUP, "").strip().lower()
        cross_quota_group = cqg_raw not in ("false", "0", "no")
        return cls(
            auth_account=e.get(cls.ENV_AUTH_ACCOUNT, ""),
            auth_backend=e.get(cls.ENV_AUTH_BACKEND, ""),
            auth_fallback_policy=e.get(cls.ENV_AUTH_FALLBACK_POLICY, ""),
            auth_policy=e.get(cls.ENV_AUTH_POLICY, ""),
            auth_include_labels=_split_csv(e.get(cls.ENV_AUTH_INCLUDE_LABELS, "")),
            auth_exclude_labels=_split_csv(e.get(cls.ENV_AUTH_EXCLUDE_LABELS, "")),
            auth_cross_quota_group=cross_quota_group,
        )

    def to_env_vars(self) -> dict[str, str]:
        """Encode for a Batch job env_vars / subprocess env dict.

        Empty-string scalar fields and empty-tuple label fields are
        OMITTED so the dict only contains keys the dispatch chain
        actually needs to forward. The receiving side (``from_env``)
        defaults missing keys to empty, so a round-trip preserves
        semantics.
        """
        out: dict[str, str] = {}
        if self.auth_account:
            out[self.ENV_AUTH_ACCOUNT] = self.auth_account
        if self.auth_backend:
            out[self.ENV_AUTH_BACKEND] = self.auth_backend
        if self.auth_fallback_policy:
            out[self.ENV_AUTH_FALLBACK_POLICY] = self.auth_fallback_policy
        if self.auth_policy:
            out[self.ENV_AUTH_POLICY] = self.auth_policy
        if self.auth_include_labels:
            out[self.ENV_AUTH_INCLUDE_LABELS] = ",".join(self.auth_include_labels)
        if self.auth_exclude_labels:
            out[self.ENV_AUTH_EXCLUDE_LABELS] = ",".join(self.auth_exclude_labels)
        # Only emit cross-quota-group when the operator opted out — the
        # default-true path stays env-var-free so existing dispatches
        # don't pick up a new env var.
        if not self.auth_cross_quota_group:
            out[self.ENV_AUTH_CROSS_QUOTA_GROUP] = "false"
        return out

    def to_cli_flags(self) -> list[str]:
        """Encode as ``--auth-*`` argv entries for ``cmd.extend(...)``.

        Empty fields produce nothing (rather than ``["--auth-account",
        ""]`` which crashes typer parsing). Multi-value labels emit
        repeated ``--auth-include-labels <label>`` / ``--auth-exclude-labels
        <label>`` pairs (mirrors typer's ``list[str]`` flag convention).
        """
        cmd: list[str] = []
        if self.auth_account:
            cmd.extend(["--auth-account", self.auth_account])
        if self.auth_backend:
            cmd.extend(["--auth-backend", self.auth_backend])
        if self.auth_fallback_policy:
            cmd.extend(["--auth-fallback-policy", self.auth_fallback_policy])
        if self.auth_policy:
            cmd.extend(["--auth-policy", self.auth_policy])
        for lbl in self.auth_include_labels:
            cmd.extend(["--auth-include-labels", lbl])
        for lbl in self.auth_exclude_labels:
            cmd.extend(["--auth-exclude-labels", lbl])
        # Boolean flag: only emit when the operator opted out so the
        # default-true path produces no flag and doesn't trip --help
        # parsing on older worker images that may not have the option.
        if not self.auth_cross_quota_group:
            cmd.append("--no-auth-cross-quota-group")
        return cmd

    def is_pool_dispatch_enabled(self) -> bool:
        """True iff this config opts into pool dispatch.

        ``auth_account`` is the master switch — without an adapter, the
        other fields are inert. Used by the dispatch path to short-circuit
        out of pool construction when the operator didn't configure one.
        """
        return bool(self.auth_account)


__all__ = ["AuthPoolFlags"]
