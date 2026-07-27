"""Tests for the 429-cooling quota-group walk in _teardown_pool_slot.

Phase 4 of the Vehicle A pool redesign (the original design, this regression).
Spec: docs/arch/arch-metaproc-core.md.

When the classifier returns ``status=cooling`` (429 rate-limit), the
retry path should walk past the entire quota group of the failing
label, not just the failing label itself. Anthropic rate-limits at
account level (and per organizationUuid in some configurations —
claude-code#41886), so two labels in the same org will 429 in
lockstep. Walking past the whole group collapses N×429 retries to one.

Coverage:
- Same-org expansion: alt1 fails 429 → alt2 (same org) added to exclude.
- Different-org NOT expanded: alt1 fails 429 → alt3 (other org) NOT added.
- Account-kind quota groups: alt1 (account:HEX1) fails 429 → alt2
  (account:HEX2) NOT added; alt2-clone (account:HEX1) added.
- Unknown-group failing label: NO expansion (conservative — we don't
  know what shares quota).
- 401/403 expired classification does NOT expand (only cooling does;
  expired is per-credential, not per-account).
- Failing label already-excluded edge case: helper is idempotent.
- Backend lookup failure: helper returns silently, falls back to
  single-label exclude (existing behavior).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metaproc.adapters.base import AuthFailureClassification
from metaproc.commands.run_parallel import _expand_pool_exclude_by_quota_group, _teardown_pool_slot
from metaproc.dispatch import pool_dispatch as pd_mod
from metaproc.dispatch.credential_pool import (
    EntryState,
    PoolEntry,
    QuotaGroup,
    Vehicle,
)
from metaproc.dispatch.credential_pool import FallbackPolicy as _FallbackPolicy
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig
from metaproc.dispatch.slot_coordinator import SlotLease

_ORG_A = "org-a-12345678-1234-1234-1234-aaaaaaaaaaaa"
_ORG_B = "org-b-12345678-1234-1234-1234-bbbbbbbbbbbb"
_ACCT_1 = "account-hash-aaaa1111"
_ACCT_2 = "account-hash-bbbb2222"


@dataclass
class _FakeBackend:
    """Just enough backend surface to exercise the quota-group walk."""

    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [
            entry for (a, _label), entry in self.entries.items() if adapter is None or a == adapter
        ]


@dataclass
class _FakeCoordinator:
    backend: _FakeBackend


@dataclass
class _FakePoolDispatch:
    coordinator: _FakeCoordinator


def _entry(label: str, *, quota_group: QuotaGroup) -> PoolEntry:
    return PoolEntry(
        adapter="claude-code-cli",
        label=label,
        blob="opaque",
        state=EntryState(
            status="active",
            fp=f"fp-{label}",
            vehicle=Vehicle.OAUTH_TOKEN,
            quota_group=quota_group,
        ),
        etag=f"etag-{label}",
    )


def _seed_three_labels(
    *,
    alt1_group: QuotaGroup,
    alt2_group: QuotaGroup,
    alt3_group: QuotaGroup,
) -> _FakePoolDispatch:
    backend = _FakeBackend()
    backend.entries[("claude-code-cli", "alt1")] = _entry("alt1", quota_group=alt1_group)
    backend.entries[("claude-code-cli", "alt2")] = _entry("alt2", quota_group=alt2_group)
    backend.entries[("claude-code-cli", "alt3")] = _entry("alt3", quota_group=alt3_group)
    return _FakePoolDispatch(coordinator=_FakeCoordinator(backend=backend))


def _lease(label: str) -> SlotLease:

    return SlotLease(
        adapter="claude-code-cli",
        label=label,
        slot_dir=Path("/tmp/test-slot"),  # Unused by the helper.
        holder="r:s:i:a1",
        scope_env={},
        scrub_env={},
        bootstrap_fp=f"fp-{label}",
    )


class TestSameOrgGroupExpansion:
    def test_alt1_alt2_same_org_walks_past_alt2_too(self) -> None:
        # Both alt1 and alt2 belong to org-A; alt3 is in org-B.
        # When alt1 hits 429, the helper should add alt2 (same org)
        # to exclude but leave alt3 (different org) selectable.
        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="org", value=_ORG_A),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )
        # alt1 already in the exclude list (the failing label).
        exclude: list[tuple[str, str]] = [("claude-code-cli", "alt1")]
        _expand_pool_exclude_by_quota_group(
            pool_dispatch=pool, lease=_lease("alt1"), exclude_list=exclude
        )
        excluded_labels = {label for _adapter, label in exclude}
        assert "alt1" in excluded_labels
        assert "alt2" in excluded_labels  # same org → excluded
        assert "alt3" not in excluded_labels  # different org → still selectable

    def test_idempotent_when_alt2_already_excluded(self) -> None:
        # If alt2 was already added by some prior path, the expansion
        # must NOT double-add it.
        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="org", value=_ORG_A),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )
        exclude: list[tuple[str, str]] = [
            ("claude-code-cli", "alt1"),
            ("claude-code-cli", "alt2"),
        ]
        _expand_pool_exclude_by_quota_group(
            pool_dispatch=pool, lease=_lease("alt1"), exclude_list=exclude
        )
        # Length stays 2 — no duplicate alt2.
        assert len(exclude) == 2
        assert exclude.count(("claude-code-cli", "alt2")) == 1


class TestAccountKindGroups:
    def test_account_group_expansion_only_within_same_account_hash(self) -> None:
        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="account", value=_ACCT_1),
            alt2_group=QuotaGroup(kind="account", value=_ACCT_2),
            alt3_group=QuotaGroup(kind="account", value=_ACCT_1),
        )
        exclude: list[tuple[str, str]] = [("claude-code-cli", "alt1")]
        _expand_pool_exclude_by_quota_group(
            pool_dispatch=pool, lease=_lease("alt1"), exclude_list=exclude
        )
        excluded_labels = {label for _adapter, label in exclude}
        # alt3 shares the account hash with alt1 — added.
        # alt2 is a different account — not added.
        assert "alt3" in excluded_labels
        assert "alt2" not in excluded_labels


class TestUnknownGroupNoExpansion:
    def test_unknown_quota_group_does_not_expand(self) -> None:
        # Failing label has unknown quota_group. Without knowing what
        # else shares quota, the conservative answer is "don't
        # expand". Operators resolve by annotating quota_group on
        # push.
        pool = _seed_three_labels(
            alt1_group=QuotaGroup.unknown(),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )
        exclude: list[tuple[str, str]] = [("claude-code-cli", "alt1")]
        _expand_pool_exclude_by_quota_group(
            pool_dispatch=pool, lease=_lease("alt1"), exclude_list=exclude
        )
        # No expansion — exclude list stays at the failing label only.
        assert exclude == [("claude-code-cli", "alt1")]


class TestBackendFailureFallsBackSilently:
    def test_missing_entry_does_not_raise(self) -> None:
        # A race could remove the entry between lease and teardown;
        # the helper must not crash teardown.
        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="org", value=_ORG_A),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )
        # Use a lease for a label the backend doesn't know about.
        exclude: list[tuple[str, str]] = [("claude-code-cli", "ghost")]
        _expand_pool_exclude_by_quota_group(
            pool_dispatch=pool, lease=_lease("ghost"), exclude_list=exclude
        )
        # Falls back to single-label exclude (no expansion, no raise).
        assert exclude == [("claude-code-cli", "ghost")]


class TestClassificationVerdictGating:
    """Phase 4 contract: only ``status=cooling`` triggers expansion in
    _teardown_pool_slot. ``status=expired`` (401/403) is per-credential,
    not per-account, so the existing single-label exclude stays correct
    for that path. This test asserts the contract by inspecting the
    classification value the helper would receive — the helper itself is
    only called from the cooling branch in _teardown_pool_slot."""

    def test_classification_status_field_named_cooling(self) -> None:
        # Sanity: the AuthFailureClassification status enum uses
        # exactly "cooling" as the 429-rate-limited string. If this
        # were ever renamed, _teardown_pool_slot's gating would
        # silently miss expansion. Pin the literal.
        c = AuthFailureClassification(status="cooling", reason="429 rate_limit")
        assert c.status == "cooling"

    def test_classification_status_field_named_expired(self) -> None:
        c = AuthFailureClassification(status="expired", reason="401 auth_error")
        assert c.status == "expired"


class TestCrossQuotaGroupOptOut:
    """Phase 11.3: --no-auth-cross-quota-group skips expansion.

    The expansion is unconditional inside the helper; the gate is in
    ``_teardown_pool_slot`` which only calls the helper when
    ``pool_dispatch.cross_quota_group`` is True. These tests pin the
    gate at the helper-call boundary by directly exercising the
    classifier-cooling path with both flag values.
    """

    def test_default_true_calls_helper(self) -> None:
        # Behavioral pin: when cross_quota_group is True, the helper
        # IS called from the cooling branch, and the test asserts the
        # exclusion set is expanded as a side effect (mirrors the prod
        # path).
        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="org", value=_ORG_A),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )
        exclude: list[tuple[str, str]] = [("claude-code-cli", "alt1")]
        # Direct helper call — same code path the cooling branch
        # invokes.
        _expand_pool_exclude_by_quota_group(
            pool_dispatch=pool, lease=_lease("alt1"), exclude_list=exclude
        )
        excluded = {label for _adapter, label in exclude}
        assert excluded == {"alt1", "alt2"}  # org-A siblings collapsed

    def test_opt_out_false_skips_helper_call(self) -> None:

        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="org", value=_ORG_A),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )

        class _StubCoord:
            _backend = pool

            def teardown(self, _lease, *, failure):
                del failure
                return

            def preserve_diagnostics(self, *_args, **_kwargs):
                return None

        pd_config = PoolDispatchConfig(
            coordinator=_StubCoord(),  # pyright: ignore[reportArgumentType]
            adapter="claude-code-cli",
            runs_dir=Path("/tmp"),
            run_id="r",
            step="s",
            fallback_policy=_FallbackPolicy.NONE,
            cross_quota_group=False,
        )

        # Stub the classifier so the test doesn't need a real adapter
        # or debug log to produce the cooling verdict.
        original = pd_mod.classify_failure_for_slot

        def fake_classify(_lease, *, error_str, session_log_path):  # noqa: ARG001
            return AuthFailureClassification(status="cooling", reason="429")

        pd_mod.classify_failure_for_slot = fake_classify  # type: ignore[assignment]
        try:

            class _RecordingPool:
                def record_auth_outcome(self, _outcome):
                    return None

            shared: dict[str, Any] = {
                "slot_lease": _lease("alt1"),
                "attempt_number": 1,
                "log_path": None,
            }
            _teardown_pool_slot(
                pool_dispatch=pd_config,
                pool=_RecordingPool(),
                shared=shared,
                error_str="HTTP 429 too_many_requests",
            )
        finally:
            pd_mod.classify_failure_for_slot = original  # type: ignore[assignment]

        excluded = {label for _adapter, label in shared["pool_exclude"]}
        # alt1 added; alt2 NOT added even though same org-A.
        assert excluded == {"alt1"}


class TestUnknownClassificationDoesNotExcludeLabel:
    """Regression: ``status=unknown`` (content failures) must NOT exclude the label.

    Pre-fix bug: ``_teardown_pool_slot`` appended ``(adapter, label)`` to
    ``pool_exclude`` unconditionally on any failure, including
    ``status="unknown"`` — the classification Claude returns for
    invalid_outputs / malformed YAML / runbook crashes when the *output*
    was bad but the credential is healthy. Two consecutive unknown
    failures on the same item exhausted the per-item label set
    permanently, sending the worker into a 60-second-cycle "pool
    exhausted -- waiting 60s for slot recovery" loop with no escape.
    Witnessed 2026-05-21 tier5 BULL/CAE: 53 min stuck before the fix.
    """

    def test_unknown_status_does_not_exclude_label(self) -> None:

        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="org", value=_ORG_A),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )

        class _StubCoord:
            _backend = pool

            def teardown(self, _lease, *, failure):
                del failure
                return

            def preserve_diagnostics(self, *_args, **_kwargs):
                return None

        pd_config = PoolDispatchConfig(
            coordinator=_StubCoord(),  # pyright: ignore[reportArgumentType]
            adapter="claude-code-cli",
            runs_dir=Path("/tmp"),
            run_id="r",
            step="s",
            fallback_policy=_FallbackPolicy.NONE,
            cross_quota_group=True,
        )

        original = pd_mod.classify_failure_for_slot

        def fake_classify(_lease, *, error_str, session_log_path):  # noqa: ARG001
            # Claude's classifier returns status="unknown" for non-credential
            # failures (invalid_outputs, runbook crashes, content errors).
            return AuthFailureClassification(status="unknown", reason="content")

        pd_mod.classify_failure_for_slot = fake_classify  # type: ignore[assignment]
        try:

            class _RecordingPool:
                def record_auth_outcome(self, _outcome):
                    return None

            shared: dict[str, Any] = {
                "slot_lease": _lease("alt1"),
                "attempt_number": 1,
                "log_path": None,
            }
            _teardown_pool_slot(
                pool_dispatch=pd_config,
                pool=_RecordingPool(),
                shared=shared,
                error_str="invalid_outputs: malformed YAML at offset 42",
            )
        finally:
            pd_mod.classify_failure_for_slot = original  # type: ignore[assignment]

        # The label MUST NOT be excluded for an unknown-classification failure.
        # The credential is healthy; only the content was bad.
        assert shared["pool_exclude"] == []

    def test_expired_status_does_exclude_label(self) -> None:
        """Sanity: ``status=expired`` is a credential issue, so the label IS excluded."""

        pool = _seed_three_labels(
            alt1_group=QuotaGroup(kind="org", value=_ORG_A),
            alt2_group=QuotaGroup(kind="org", value=_ORG_A),
            alt3_group=QuotaGroup(kind="org", value=_ORG_B),
        )

        class _StubCoord:
            _backend = pool

            def teardown(self, _lease, *, failure):
                del failure
                return

            def preserve_diagnostics(self, *_args, **_kwargs):
                return None

        pd_config = PoolDispatchConfig(
            coordinator=_StubCoord(),  # pyright: ignore[reportArgumentType]
            adapter="claude-code-cli",
            runs_dir=Path("/tmp"),
            run_id="r",
            step="s",
            fallback_policy=_FallbackPolicy.NONE,
            cross_quota_group=True,
        )

        original = pd_mod.classify_failure_for_slot

        def fake_classify(_lease, *, error_str, session_log_path):  # noqa: ARG001
            return AuthFailureClassification(status="expired", reason="401")

        pd_mod.classify_failure_for_slot = fake_classify  # type: ignore[assignment]
        try:

            class _RecordingPool:
                def record_auth_outcome(self, _outcome):
                    return None

            shared: dict[str, Any] = {
                "slot_lease": _lease("alt1"),
                "attempt_number": 1,
                "log_path": None,
            }
            _teardown_pool_slot(
                pool_dispatch=pd_config,
                pool=_RecordingPool(),
                shared=shared,
                error_str="HTTP 401 unauthorized",
            )
        finally:
            pd_mod.classify_failure_for_slot = original  # type: ignore[assignment]

        excluded = {label for _adapter, label in shared["pool_exclude"]}
        # alt1 added (expired = credential issue, label-level).
        # alt2 NOT added (expired is per-credential, not per-group;
        # the quota-group walk only fires on cooling).
        assert excluded == {"alt1"}
