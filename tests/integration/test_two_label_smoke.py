"""Two-label end-to-end smoke for the auth credential pool.

Covers P2b.6 from `plan-2026-04-21-auth-credential-pool.md` and the four
P0 framework fixes that landed on `dispatch/2026-04-28-tuesday`:

- the selector and fallback regressions — selector primary path used on every attempt
  and respects exclude_labels.
- the fix (TBD-B) — select_primary raises on missing explicit_label.
- the fix — run-process wires --auth-account / --auth-include-labels.
- the fix — cooling-aware retry helper.

Uses an in-memory ``LocalFilesystemBackend`` rooted in tmp_path so the
test never touches the real ``~/.metaproc/credentials.json``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from metaproc.commands.run_parallel import _compute_pool_cooling_delay
from metaproc.dispatch.credential_pool import (
    EntryState,
    FallbackPolicy,
    LocalFilesystemBackend,
    SelectionPolicy,
    SelectionStrategy,
)
from metaproc.dispatch.pool_dispatch import PoolDispatchConfig
from metaproc.dispatch.slot_coordinator import SlotCoordinator


def _fake_claude_oauth_blob(tag: str) -> str:
    """Minimal claude-code-cli credential blob shape (claudeAiOauth wrapper)."""

    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": f"acc-{tag}",
                "refreshToken": f"ref-{tag}",
                "expiresAt": int(time.time()) + 3600,
                "scopes": ["user:profile"],
            }
        }
    )


def _push(backend: LocalFilesystemBackend, label: str, *, fp: str, tag: str) -> None:
    blob = _fake_claude_oauth_blob(tag)
    state = EntryState(status="active", fp=fp, last_ok_ts=int(time.time()))
    backend.upsert_entry("claude-code-cli", label, blob=blob, state=state)


@pytest.fixture
def two_label_pool(tmp_path: Path) -> LocalFilesystemBackend:
    """A pool with two active labels (alt1, alt2) — Tuesday's worker pool shape."""
    backend = LocalFilesystemBackend(path=tmp_path / "credentials.json")
    _push(backend, "alt1", fp="3b9767a228fe", tag="alt1")
    _push(backend, "alt2", fp="f024c6aa58d9", tag="alt2")
    return backend


@pytest.fixture
def coord(two_label_pool: LocalFilesystemBackend) -> SlotCoordinator:
    return SlotCoordinator(two_label_pool)


@pytest.fixture
def lease_kwargs(tmp_path: Path) -> dict[str, Any]:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return {
        "runs_dir": runs_dir,
        "run_id": "two-label-smoke",
        "step": "process-item",
        "item": "item-1",
    }


class TestTwoLabelWorkerPoolShape:
    """The baseline pool exposes both configured labels as eligible."""

    def test_two_labels_eligible(self, two_label_pool: LocalFilesystemBackend) -> None:
        entries = two_label_pool.list_entries(adapter="claude-code-cli")
        labels = sorted(e.label for e in entries)
        assert labels == ["alt1", "alt2"]
        assert all(e.state.status == "active" for e in entries)

    def test_distinct_fingerprints(self, two_label_pool: LocalFilesystemBackend) -> None:
        # Confirms each label is genuinely a different account, not duplicated.
        entries = two_label_pool.list_entries(adapter="claude-code-cli")
        fps = {e.state.fp for e in entries}
        assert len(fps) == 2


class TestPrimaryLeaseAcrossAttempts:
    """Phase 1 P0: the selector and fallback regressions — selector primary path."""

    def test_attempt_zero_primary(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord.acquire_slot(adapter="claude-code-cli", attempt=0, **lease_kwargs)
        assert lease is not None
        assert lease.label in {"alt1", "alt2"}
        coord.teardown(lease, failure=None)

    def test_attempt_one_still_uses_primary(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        # this regression: attempt=1 (real first attempt from
        # run_parallel) used to skip primary and fall through to
        # FallbackPolicy.NONE → None. After the fix, primary is tried
        # on every attempt.
        lease = coord.acquire_slot(
            adapter="claude-code-cli",
            attempt=1,
            fallback_policy=FallbackPolicy.NONE,
            **lease_kwargs,
        )
        assert lease is not None, "jdfp regression — attempt=1+NONE should still pick primary"
        coord.teardown(lease, failure=None)

    def test_exclude_at_attempt_zero_picks_alternate(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        # the fix (TBD-A) regression: at attempt=0 with one label
        # excluded, the previous guard `not exclude` short-circuited to
        # fallback. Now: primary respects exclude and finds the next
        # eligible label.
        lease = coord.acquire_slot(
            adapter="claude-code-cli",
            attempt=0,
            exclude=(("claude-code-cli", "alt1"),),
            fallback_policy=FallbackPolicy.NONE,
            **lease_kwargs,
        )
        assert lease is not None
        assert lease.label == "alt2"
        coord.teardown(lease, failure=None)


class TestExplicitLabelHandling:
    """Phase 1 P0: the fix (TBD-B) — explicit_label raises on missing."""

    def test_explicit_label_present(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord.acquire_slot(
            adapter="claude-code-cli",
            attempt=0,
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt2",)),
            **lease_kwargs,
        )
        assert lease is not None
        assert lease.label == "alt2"
        coord.teardown(lease, failure=None)

    def test_explicit_label_missing_raises_with_alternatives(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        # Operator typo on --auth-account / strategy labels. The error
        # must name what they asked for AND list available labels so
        # they can fix the command without hunting through pool state.
        with pytest.raises(KeyError) as excinfo:
            coord.acquire_slot(
                adapter="claude-code-cli",
                attempt=0,
                strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("laptop",)),
                **lease_kwargs,
            )
        msg = excinfo.value.args[0]
        assert "laptop" in msg
        # The error lists the actually-available labels (alt1, alt2).
        assert "alt1" in msg
        assert "alt2" in msg


class TestSlotMaterialization:
    """Per-lease scope: scrub_env strips ambient OAuth vars; cred path under slot_dir."""

    def test_scope_env_sets_claude_config_dir(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord.acquire_slot(adapter="claude-code-cli", attempt=0, **lease_kwargs)
        assert lease is not None
        assert "CLAUDE_CONFIG_DIR" in lease.scope_env
        config_dir = Path(lease.scope_env["CLAUDE_CONFIG_DIR"])
        cred_path = config_dir / ".credentials.json"
        assert cred_path.exists(), (
            "the lease should materialize the OAuth blob under CLAUDE_CONFIG_DIR/.credentials.json"
        )
        coord.teardown(lease, failure=None)

    def test_scrub_env_lists_ambient_oauth_vars(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        lease = coord.acquire_slot(adapter="claude-code-cli", attempt=0, **lease_kwargs)
        assert lease is not None
        # These three would let an ambient ENV value override the leased
        # credential. The scrub list ensures the worker process sees a
        # clean slate where only the leased blob is authoritative.
        scrub_keys = set(lease.scrub_env.keys())
        assert "ANTHROPIC_AUTH_TOKEN" in scrub_keys
        assert "CLAUDE_CODE_OAUTH_TOKEN" in scrub_keys
        assert "CLAUDE_CODE_APIKEY_HELPER" in scrub_keys
        coord.teardown(lease, failure=None)


class TestExhaustionAndRecovery:
    """Phase 1 P0: the fix — orchestrator survives full pool exhaustion."""

    def test_exclude_all_returns_none(
        self, coord: SlotCoordinator, lease_kwargs: dict[str, Any]
    ) -> None:
        # Caller deliberately excluded both labels (e.g. inside a retry
        # walk that already tried each). The dispatch caller decides whether
        # to fail or reschedule when no lease is returned.
        lease = coord.acquire_slot(
            adapter="claude-code-cli",
            attempt=0,
            exclude=(("claude-code-cli", "alt1"), ("claude-code-cli", "alt2")),
            fallback_policy=FallbackPolicy.NONE,
            **lease_kwargs,
        )
        assert lease is None

    def test_cooling_delay_helper_picks_earliest_cooling(self, tmp_path: Path) -> None:

        backend = LocalFilesystemBackend(path=tmp_path / "credentials.json")
        now = time.time()
        backend.upsert_entry(
            "claude-code-cli",
            "alt1",
            blob=_fake_claude_oauth_blob("alt1"),
            state=EntryState(
                status="cooling",
                fp="3b9767a228fe",
                cooling_until_ts=int(now + 200),
            ),
        )
        backend.upsert_entry(
            "claude-code-cli",
            "alt2",
            blob=_fake_claude_oauth_blob("alt2"),
            state=EntryState(
                status="cooling",
                fp="f024c6aa58d9",
                cooling_until_ts=int(now + 800),
            ),
        )
        config = PoolDispatchConfig(
            coordinator=SlotCoordinator(backend),
            adapter="claude-code-cli",
            runs_dir=tmp_path / "runs",
            run_id="tuesday",
            step="predict-ticker",
        )
        delay = _compute_pool_cooling_delay(config, floor_s=1.0, ceiling_s=1800.0, jitter_s=30.0)
        # Within ~30s of the earliest reset (alt1 at +200s).
        assert 200 <= delay <= 260, (
            f"expected delay near earliest cooling reset (~230s), got {delay:.1f}s"
        )
