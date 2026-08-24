"""Tests for metaproc.dispatch.pool_dispatch + run_parallel integration.

Covers P2.4 (integration hook) + part of P2.5 (auth_outcome construction).
Full end-to-end P2.7 integration tests against a mocked RunPool retry
heap land separately.
"""

from __future__ import annotations

import inspect
import json
import logging
import time as _time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest

import metaproc.adapters.registry as reg
import metaproc.dispatch.pool_dispatch as pd
from metaproc.adapters.base import AuthFailureClassification, FailureSeverity
from metaproc.adapters.claude_code import ClaudeApiSignals, ClaudeCodeCliAdapter
from metaproc.commands.run_parallel import (
    _build_prepare_launch,
    _compute_pool_cooling_delay,
    _run_agent_pool,
    _teardown_pool_slot,
)
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    FallbackPolicy,
    PoolEntry,
    SelectionPolicy,
    SelectionStrategy,
    fingerprint_blob,
)
from metaproc.dispatch.credential_pool import Vehicle as _Vehicle
from metaproc.dispatch.known_bugs import detect_known_bug
from metaproc.dispatch.pool_dispatch import (
    AuthOutcome,
    PoolAuthOverrideError,
    PoolDispatchConfig,
    PoolSlotUnavailableError,
    acquire_slot,
    auth_override_refusal_keys,
    build_auth_lease_acquired,
    build_auth_outcome,
    classify_failure_for_slot,
    complete_slot,
    compose_slot_env,
    pre_fan_out_probe,
    probe_credential,
)
from metaproc.dispatch.pool_dispatch import (
    auth_forces_abort as _auth_forces_abort,
)
from metaproc.dispatch.slot_coordinator import (
    SLOT_ACTIVE_ENV_VAR,
    SlotCoordinator,
    SlotLease,
)
from metaproc.runpool.events import EventLogger

# ── Pool + adapter fakes (shared shape with test_slot_coordinator) ─


@dataclass
class _InMemoryPool:
    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    counter: int = 0

    def _next_etag(self) -> str:
        self.counter += 1
        return f"et-{self.counter}"

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        key = (adapter, label)
        if key not in self.entries:
            raise KeyError(f"missing {adapter}/{label}")
        return self.entries[key]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def upsert_entry(self, adapter, label, *, blob, state, expected_etag=None):
        key = (adapter, label)
        existing = self.entries.get(key)
        if expected_etag is not None and existing is not None and existing.etag != expected_etag:
            raise ConcurrentModificationError(f"stale {expected_etag} != {existing.etag}")
        new_blob = blob if blob is not None else (existing.blob if existing else "")
        new_state = (
            state
            if state is not None
            else (existing.state if existing else EntryState(status="active", fp=""))
        )
        new_etag = self._next_etag()
        self.entries[key] = PoolEntry(
            adapter=adapter, label=label, blob=new_blob, state=new_state, etag=new_etag
        )
        return new_etag

    def delete_entry(self, adapter, label):
        self.entries.pop((adapter, label), None)

    def seed(self, adapter, label, *, state, blob="blob"):
        self.upsert_entry(adapter, label, blob=blob, state=state)


class _StubAdapter:
    adapter_type = "claude-code-cli"
    short_name = "claude-cli"
    default_model = None
    slot_credential_filename = ".credentials.json"
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def __init__(self, flush_rotates_to: str | None = None) -> None:
        self.flush_rotates_to = flush_rotates_to

    def build_command(self, *args: Any, **kwargs: Any) -> list[str]:
        return ["claude"]

    def prepare_env(self, env: dict[str, str], _cfg: dict[str, object]) -> dict[str, str]:
        return env

    def working_directory(self, _cfg: dict[str, object]) -> Path | None:
        return None

    def parse_result_event(self, _line: str) -> dict[str, object] | None:
        return None

    def check_auth(self) -> Any:
        return None

    def auth_info(self) -> str:
        return ""

    def validate_config(self, _cfg: dict[str, object]) -> list[Any]:
        return []

    def bootstrap(self, _home: Path) -> None:
        pass

    def credential_scope_env(
        self, slot_dir: Path, *, vehicle: object = None, blob: str = ""
    ) -> dict[str, str]:
        del vehicle, blob
        return {"CLAUDE_CONFIG_DIR": str(slot_dir)}

    def credential_scrub_env(self, *, vehicle: object = None) -> dict[str, str]:
        del vehicle
        return {
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_APIKEY_HELPER": "",
        }

    def materialize_credential(self, slot_dir: Path, blob: str, *, vehicle: object = None) -> None:
        del vehicle
        slot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (slot_dir / self.slot_credential_filename).write_text(blob)
        (slot_dir / self.slot_credential_filename).chmod(0o600)

    def capture_credential(self) -> str:
        return '{"claudeAiOauth":{}}'

    def classify_failure(
        self, _exc: BaseException | None, stderr: str, _session_log_path: Path | None
    ) -> AuthFailureClassification:
        if "too_many_requests" in stderr:
            return AuthFailureClassification(
                status="cooling", cooling_until_ts=1234, reason="rate-limit"
            )
        if "invalid_grant" in stderr:
            return AuthFailureClassification(status="expired", reason="invalid_grant")
        return AuthFailureClassification(status="unknown")

    def flush_refreshed_credential(self, slot_dir: Path) -> str | None:
        return self.flush_rotates_to

    def query_quota_usage(self, _slot_dir: Path) -> Any:
        return None

    def query_live_quota(self, _slot_dir: Path) -> Any:
        return None

    def debug_capture_args(self, slot_dir: Path) -> list[str]:
        return ["-d", "api", "--debug-file", str(slot_dir / "claude-code-debug.log")]

    def diagnostic_filenames(self) -> tuple[str, ...]:
        return ("claude-code-debug.log",)

    def setup_token_command(self) -> list[str] | None:
        return None


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def pool_and_adapter(tmp_path):
    pool = _InMemoryPool()
    adapter = _StubAdapter()
    # Seed two labels so fallback paths have somewhere to go.
    pool.seed(
        "claude-code-cli",
        "laptop",
        state=EntryState(status="active", fp=fingerprint_blob("laptop-blob")),
        blob="laptop-blob",
    )
    pool.seed(
        "claude-code-cli",
        "home",
        state=EntryState(status="active", fp=fingerprint_blob("home-blob")),
        blob="home-blob",
    )
    coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": adapter})
    return pool, adapter, coord, tmp_path


# ── PoolDispatchConfig + acquire_slot ───────────────────────────


class TestAcquireSlot:
    def test_acquires_under_none_policy_on_attempt_zero(self, pool_and_adapter, monkeypatch):

        _pool, adapter, coord, tmp_path = pool_and_adapter
        monkeypatch.setattr(
            reg, "get_auth_capable", lambda name: adapter if name == "claude-code-cli" else None
        )

        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.NONE,
        )
        lease = acquire_slot(config, item="AAPL", attempt=0)
        assert lease.adapter == "claude-code-cli"
        assert lease.scope_env == {"CLAUDE_CONFIG_DIR": str(lease.slot_dir)}

    def test_explicit_label_honored_on_attempt_zero(self, pool_and_adapter, monkeypatch):

        _pool, adapter, coord, tmp_path = pool_and_adapter
        monkeypatch.setattr(
            reg, "get_auth_capable", lambda name: adapter if name == "claude-code-cli" else None
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("home",)),
            fallback_policy=FallbackPolicy.NONE,
        )
        lease = acquire_slot(config, item="AAPL", attempt=0)
        assert lease.label == "home"

    def test_attempt_gt_zero_uses_fallback_walk(self, pool_and_adapter, monkeypatch):

        _pool, adapter, coord, tmp_path = pool_and_adapter
        monkeypatch.setattr(
            reg, "get_auth_capable", lambda name: adapter if name == "claude-code-cli" else None
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
            exclude=(("claude-code-cli", "laptop"),),
        )
        # attempt=0 is ignored when exclude is non-empty — coordinator
        # uses fallback even at attempt 0 if exclude is populated.
        lease = acquire_slot(config, item="AAPL", attempt=1)
        assert lease.label == "home"

    def test_raises_when_no_eligible(self, pool_and_adapter, monkeypatch):

        _pool, adapter, coord, tmp_path = pool_and_adapter
        monkeypatch.setattr(
            reg, "get_auth_capable", lambda name: adapter if name == "claude-code-cli" else None
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
            # Exclude BOTH seeded labels.
            exclude=(
                ("claude-code-cli", "laptop"),
                ("claude-code-cli", "home"),
            ),
        )
        with pytest.raises(PoolSlotUnavailableError) as exc_info:
            acquire_slot(config, item="AAPL", attempt=1)
        assert exc_info.value.adapter == "claude-code-cli"
        assert exc_info.value.policy == FallbackPolicy.SAME_PROVIDER


class TestCompleteSlot:
    def test_classifier_failure_still_tears_down_lease(
        self, pool_and_adapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pool, _adapter, coordinator, tmp_path = pool_and_adapter
        config = PoolDispatchConfig(
            coordinator=coordinator,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("laptop",)),
        )
        lease = acquire_slot(config, item="AAPL", attempt=1)

        def fail_classification(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("classifier crash")

        monkeypatch.setattr(pd, "classify_failure_for_slot", fail_classification)

        with pytest.raises(RuntimeError, match="classifier crash"):
            complete_slot(
                config,
                lease,
                error_str="exit code 1",
                session_log_path=None,
                retry_count=0,
                retry_exclude=[],
            )

        assert not lease.slot_dir.exists()
        assert coordinator.active_counter.snapshot()[(lease.adapter, lease.label)] == 0
        assert pool.get_entry(lease.adapter, lease.label).state.status == "active"


# ── compose_slot_env ────────────────────────────────────────────


class TestComposeSlotEnv:
    def _lease(self, tmp_path):
        return SlotLease(
            adapter="claude-code-cli",
            label="laptop",
            slot_dir=tmp_path,
            holder="h",
            scope_env={"CLAUDE_CONFIG_DIR": str(tmp_path)},
            scrub_env={"ANTHROPIC_AUTH_TOKEN": "", "CLAUDE_CODE_OAUTH_TOKEN": ""},
            bootstrap_fp="fp",
        )

    def test_scope_and_scrub_applied(self, tmp_path):
        lease = self._lease(tmp_path)
        env = compose_slot_env(
            {"ANTHROPIC_AUTH_TOKEN": "leaked", "PATH": "/bin"},
            lease=lease,
        )
        assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path)
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env[SLOT_ACTIVE_ENV_VAR] == "1"
        assert env["PATH"] == "/bin"

    def test_refuse_on_keys_raises_before_env_is_composed(self, tmp_path, caplog):
        lease = self._lease(tmp_path)
        with caplog.at_level("WARNING"):
            with pytest.raises(PoolAuthOverrideError, match="ANTHROPIC_API_KEY"):
                compose_slot_env(
                    {"ANTHROPIC_API_KEY": "sk-live"},
                    lease=lease,
                    refuse_on_keys=("ANTHROPIC_API_KEY",),
                )
        assert any("ANTHROPIC_API_KEY" in r.message for r in caplog.records)

    def test_override_refusal_keys_are_adapter_specific(self):
        assert auth_override_refusal_keys("claude-code-cli") == ("ANTHROPIC_API_KEY",)
        assert auth_override_refusal_keys("codex-cli") == ("OPENAI_API_KEY",)
        assert auth_override_refusal_keys("pi-cli") == ()


# ── classify_failure_for_slot ───────────────────────────────────


class TestClassifyFailureForSlot:
    def test_resolves_adapter_and_classifies_rate_limit(
        self, pool_and_adapter, monkeypatch, tmp_path
    ):

        _, adapter, _, _ = pool_and_adapter
        # Patch the bound reference inside pool_dispatch (the module
        # imported get_auth_capable at top-level, so patching the
        # registry alone doesn't reach it).
        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: adapter if name == "claude-code-cli" else None,
        )
        lease = SlotLease(
            adapter="claude-code-cli",
            label="laptop",
            slot_dir=tmp_path,
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp="fp",
        )
        result = classify_failure_for_slot(lease, error_str="HTTP 429 too_many_requests")
        assert result.status == "cooling"
        assert result.cooling_until_ts == 1234

    def test_unknown_when_adapter_not_resolvable(self, pool_and_adapter, monkeypatch, tmp_path):

        monkeypatch.setattr(pd, "get_auth_capable", lambda _: None)
        lease = SlotLease(
            adapter="ghost-cli",
            label="laptop",
            slot_dir=tmp_path,
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp="fp",
        )
        result = classify_failure_for_slot(lease, error_str="anything")
        assert result.status == "unknown"

    def test_output_validation_failure_skips_adapter_classifier(
        self, pool_and_adapter, monkeypatch, tmp_path
    ):

        adapter_called = []

        class _SpyAdapter:
            def classify_failure(self, exc, error_str, session_log_path):
                adapter_called.append(error_str)

                # If reached, returns the buggy mislabel — test must NOT see this.
                return AuthFailureClassification(
                    status="unknown", reason="transient-network-or-5xx"
                )

        monkeypatch.setattr(pd, "get_auth_capable", lambda _name: _SpyAdapter())
        lease = SlotLease(
            adapter="claude-code-cli",
            label="alt1",
            slot_dir=tmp_path,
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp="fp",
        )
        result = classify_failure_for_slot(
            lease,
            error_str=(
                "output validation failed: required field 'edge_scenario_analysis' "
                "missing; timeout while reading source-cache.md"
            ),
        )
        assert result.status == "unknown"
        assert result.reason == "invalid_outputs"
        assert adapter_called == [], "adapter classifier must not run for content failures"

        assert result.status == "unknown"

    def test_prepends_slot_local_claude_debug_log_to_error_str(
        self, pool_and_adapter, monkeypatch, tmp_path
    ):

        seen = {}

        def fake_classify(_self, _exc, error_str, _session_log_path):
            seen["error_str"] = error_str

            return AuthFailureClassification(status="unknown")

        # Use a no-op adapter we control; pool_dispatch resolves
        # `claude-code-cli` to it.
        class _NoOpAdapter:
            def classify_failure(self, exc, error_str, session_log_path):
                return fake_classify(self, exc, error_str, session_log_path)

        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: _NoOpAdapter() if name == "claude-code-cli" else None,
        )

        slot_dir = tmp_path / "slot"
        slot_dir.mkdir()
        debug_content = (
            "2026-04-27T18:08:18Z [DEBUG] [API:auth] OAuth token check starting\n"
            "2026-04-27T18:08:18Z [ERROR] AxiosError: "
            "[url=https://platform.claude.com/v1/oauth/token, status=400]\n"
            "2026-04-27T18:08:18Z [ERROR] API error (attempt 1/11): 401\n"
        )
        (slot_dir / "claude-code-debug.log").write_text(debug_content)

        lease = SlotLease(
            adapter="claude-code-cli",
            label="alt1",
            slot_dir=slot_dir,
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp="fp",
        )
        classify_failure_for_slot(lease, error_str="exit code 1")
        # Adapter received both: the original error_str (first) and the
        # debug-log content (appended after). AxiosError + per-attempt API
        # error are visible to the classifier's priority-1 check. The
        # append-after ordering protects forward-only regex lookaheads in
        # known-bug detectors from matching tokens they should exclude.
        assert "AxiosError" in seen["error_str"]
        assert "OAuth token check" in seen["error_str"]
        assert "exit code 1" in seen["error_str"]
        # Order check: the engine error_str must come BEFORE the debug-log
        # content so forward-only negative lookaheads in known-bug regexes
        # (e.g. `claude-startup-exit-1-silent`) see diagnostic tokens like
        # `429`, `rate_limit_error`, `error` AFTER the `exit code 1` match
        # position and can correctly exclude rate-limit failures from the
        # silent-startup bug pattern. See arch-claude-code-harness.md
        # § "False-positive classifier pitfall".
        assert seen["error_str"].index("exit code 1") < seen["error_str"].index("AxiosError")

    def test_rate_limit_in_debug_log_does_not_match_silent_startup_known_bug(
        self, pool_and_adapter, monkeypatch, tmp_path
    ):

        seen = {}

        def fake_classify(_self, _exc, error_str, _session_log_path):
            seen["error_str"] = error_str
            seen["matched_bug"] = detect_known_bug(stderr=error_str)

            return AuthFailureClassification(status="unknown")

        class _NoOpAdapter:
            def classify_failure(self, exc, error_str, session_log_path):
                return fake_classify(self, exc, error_str, session_log_path)

        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: _NoOpAdapter() if name == "claude-code-cli" else None,
        )

        slot_dir = tmp_path / "slot"
        slot_dir.mkdir()
        # Synthetic debug log mirroring what we saw in production on
        # 2026-05-23: successful Read/Bash calls, then a 429 mid-session.
        debug_content = (
            "2026-05-23T16:49:30Z [DEBUG] [init] starting\n"
            "2026-05-23T16:49:35Z [DEBUG] [API:claude] tool=Read ok\n"
            "2026-05-23T16:49:42Z [DEBUG] [API:claude] tool=Bash ok\n"
            "2026-05-23T16:50:01Z [ERROR] API error: status=429 body="
            '{"type":"error","error":{"type":"rate_limit_error",'
            '"message":"You are out of extra usage. Resets 11:40am"}}\n'
        )
        (slot_dir / "claude-code-debug.log").write_text(debug_content)

        lease = SlotLease(
            adapter="claude-code-cli",
            label="alt1",
            slot_dir=slot_dir,
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp="fp",
        )
        classify_failure_for_slot(lease, error_str="exit code 1")
        assert "429" in seen["error_str"]
        assert "rate_limit_error" in seen["error_str"]
        # The fix: with `exit code 1` BEFORE the debug-log tokens, the
        # known-bug regex's forward-only lookaheads correctly exclude
        # the match. detect_known_bug returns None instead of misfiring.
        assert seen["matched_bug"] is None, (
            f"Expected known-bug regex to NOT match a 429 rate-limit failure, "
            f"but detect_known_bug returned: {seen['matched_bug']}. "
            f"This is the classifier prepend-order regression — see "
            f"arch-claude-code-harness.md § 'False-positive classifier pitfall'."
        )

    def test_no_debug_log_does_not_alter_error_str(self, pool_and_adapter, monkeypatch, tmp_path):

        seen = {}

        class _NoOpAdapter:
            def classify_failure(self, _exc, error_str, _slp):
                seen["error_str"] = error_str

                return AuthFailureClassification(status="unknown")

        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda _name: _NoOpAdapter(),
        )
        slot_dir = tmp_path / "empty-slot"
        slot_dir.mkdir()
        lease = SlotLease(
            adapter="claude-code-cli",
            label="alt1",
            slot_dir=slot_dir,
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp="fp",
        )
        classify_failure_for_slot(lease, error_str="just stderr")
        assert seen["error_str"] == "just stderr"


# ── AuthOutcome composition ─────────────────────────────────────


class TestAuthOutcome:
    def _lease(self, tmp_path):
        return SlotLease(
            adapter="claude-code-cli",
            label="laptop",
            slot_dir=tmp_path / "slot",
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp=fingerprint_blob("original"),
        )

    def test_success_no_rotation(self, tmp_path):
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=None,
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.classification == "ok"
        assert oc.rotated is False
        assert oc.flush_fp == ""

    def test_success_with_rotation(self, tmp_path):
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=None,
            flushed_blob="rotated-blob",
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.classification == "ok"
        assert oc.rotated is True
        assert oc.flush_fp == fingerprint_blob("rotated-blob")

    def test_cooling_preserves_reset_ts(self, tmp_path):
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=AuthFailureClassification(
                status="cooling", cooling_until_ts=9999, reason="rate-limit"
            ),
            flushed_blob=None,
            retry_count=1,
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
        )
        assert oc.classification == "cooling"
        assert oc.cooling_until_ts == 9999
        assert oc.retry_count == 1
        assert oc.fallback_policy == "same-provider"

    def test_expired_carries_reason(self, tmp_path):
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=AuthFailureClassification(status="expired", reason="invalid_grant"),
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.classification == "expired"
        assert oc.reason == "invalid_grant"

    def test_auth_outcome_schema_version(self):
        # Schema v2 (plan-2026-05-03) added the run_id/step_id/item/
        # attempt/session_log_path join keys. Existing v1 readers
        # tolerate the additive change; v2 readers see legacy events
        # with the new fields defaulting to "" / 0 / "".
        assert AuthOutcome().schema_version == 2

    def test_auth_outcome_v2_join_keys_default_empty(self):
        oc = AuthOutcome()
        assert oc.run_id == ""
        assert oc.step_id == ""
        assert oc.item == ""
        assert oc.attempt == 0
        assert oc.session_log_path == ""

    def test_build_auth_outcome_propagates_join_keys_from_lease(self, tmp_path):
        # Plan §auth_lease_acquired event + extended auth_outcome schema:
        # build_auth_outcome reads the five join keys directly off the
        # lease (no caller plumbing) so post-hoc analysis can pair an
        # acquisition with its outcome by primary key.
        session_log = tmp_path / "logs" / "predict_AAPL_2026-05-04.jsonl"
        lease = SlotLease(
            adapter="claude-code-cli",
            label="alt2",
            slot_dir=tmp_path / "slot",
            holder="run-x:predict-ticker:AAPL:a1",
            scope_env={},
            scrub_env={},
            bootstrap_fp=fingerprint_blob("blob"),
            run_id="run-x",
            step_id="predict-ticker",
            item="AAPL",
            attempt=1,
            session_log_path=session_log,
        )
        oc = build_auth_outcome(
            lease,
            classification=None,
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.run_id == "run-x"
        assert oc.step_id == "predict-ticker"
        assert oc.item == "AAPL"
        assert oc.attempt == 1
        assert oc.session_log_path == str(session_log)

    def test_build_auth_outcome_session_log_path_empty_when_lease_has_none(self, tmp_path):
        # Backwards compatibility: leases constructed before run_parallel
        # threaded session_log_path through (e.g. legacy probe path) leave
        # it as None — outcome serializes it as the empty string so JSONL
        # readers don't see "None" textual junk.
        lease = SlotLease(
            adapter="claude-code-cli",
            label="alt1",
            slot_dir=tmp_path / "slot",
            holder="legacy",
            scope_env={},
            scrub_env={},
            bootstrap_fp=fingerprint_blob("blob"),
        )
        oc = build_auth_outcome(
            lease,
            classification=None,
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.session_log_path == ""
        assert oc.run_id == ""
        assert oc.attempt == 0

    def test_auth_outcome_http_fields_default_to_empty(self):
        oc = AuthOutcome()
        assert oc.request_id == ""
        assert oc.api_status is None
        assert oc.oauth_refresh_status is None
        assert oc.error_body_excerpt == ""
        assert oc.retry_after_s is None

    def test_build_auth_outcome_propagates_http_fields_from_classification(self, tmp_path):
        # OAuth refresh 400 + API 401 fixture: AuthOutcome carries every
        # field verbatim so pool-events analysis answers "what did the
        # server say?" without re-reading the slot's claude-code-debug.log
        # (which was rm -rf'd at teardown).
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=AuthFailureClassification(
                status="expired",
                reason="authentication_error/invalid_grant | Failed to authenticate. API Error: 401 ...",
                api_status=401,
                oauth_refresh_status=400,
                request_id="req_011CaUtiBrZSdjdJSW2YRSqM",
                error_body_excerpt='Failed to authenticate. API Error: 401 {"type":"error","error":{"type":"authentication_error"}}',
                retry_after_s=None,
            ),
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.classification == "expired"
        assert oc.api_status == 401
        assert oc.oauth_refresh_status == 400
        assert oc.request_id == "req_011CaUtiBrZSdjdJSW2YRSqM"
        assert "authentication_error" in oc.error_body_excerpt
        assert oc.retry_after_s is None

    def test_build_auth_outcome_propagates_retry_after_for_429(self, tmp_path):
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=AuthFailureClassification(
                status="cooling",
                cooling_until_ts=2000,
                reason="too_many_requests/overloaded",
                api_status=429,
                retry_after_s=42,
                request_id="req_429abc",
            ),
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
        )
        assert oc.classification == "cooling"
        assert oc.api_status == 429
        assert oc.retry_after_s == 42
        assert oc.oauth_refresh_status is None
        assert oc.request_id == "req_429abc"

    def test_build_auth_outcome_no_classification_leaves_http_fields_empty(self, tmp_path):
        # Successful run: classification is None; HTTP fields stay at
        # defaults so downstream events don't claim signals that weren't
        # observed.
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=None,
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.api_status is None
        assert oc.oauth_refresh_status is None
        assert oc.request_id == ""
        assert oc.error_body_excerpt == ""
        assert oc.retry_after_s is None

    def test_auth_outcome_defaults_to_pool_enabled(self):
        assert AuthOutcome().pool_enabled is True

    # Phase 5: severity + known_bug_signature plumbed through.

    def test_severity_defaults_empty_on_success(self, tmp_path):
        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=None,
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.severity == ""
        assert oc.known_bug_signature == ""

    def test_severity_derived_from_cooling_status(self, tmp_path):

        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=AuthFailureClassification(status="cooling"),
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.severity == FailureSeverity.RETRY_AFTER_WAIT

    def test_severity_explicit_passed_through(self, tmp_path):

        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=AuthFailureClassification(
                status="expired",
                reason="invalid_grant",
                severity=FailureSeverity.ABORT,
            ),
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.severity == FailureSeverity.ABORT

    def test_known_bug_signature_recorded(self, tmp_path):

        lease = self._lease(tmp_path)
        oc = build_auth_outcome(
            lease,
            classification=AuthFailureClassification(
                status="unknown",
                reason="known-bug:retired-packet-yaml-path",
                severity=FailureSeverity.ABORT,
                known_bug_signature="retired-packet-yaml-path",
            ),
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert oc.known_bug_signature == "retired-packet-yaml-path"
        # Operators grep outcomes by signature AND severity — both
        # must be populated even though the classifier used status=unknown.
        assert oc.severity == FailureSeverity.ABORT


class TestBuildAuthLeaseAcquired:
    """Schema-v2 acquisition-time event payload (plan-2026-05-03).

    Companion to AuthOutcome — emitted before the subprocess starts so
    post-hoc analysis can pair acquisitions with outcomes by primary
    key (run_id/step_id/item/attempt/session_log_path) instead of
    timestamp inference.
    """

    def _lease_with_keys(self, tmp_path):
        return SlotLease(
            adapter="claude-code-cli",
            label="alt2",
            slot_dir=tmp_path / "slot",
            holder="run-x:predict-ticker:AAPL:a1",
            scope_env={},
            scrub_env={},
            bootstrap_fp=fingerprint_blob("blob"),
            run_id="run-x",
            step_id="predict-ticker",
            item="AAPL",
            attempt=1,
            session_log_path=tmp_path / "session.jsonl",
        )

    def test_payload_carries_join_keys_from_lease(self, tmp_path):
        lease = self._lease_with_keys(tmp_path)
        payload = build_auth_lease_acquired(lease)

        assert payload["schema_version"] == 2
        assert payload["adapter"] == "claude-code-cli"
        assert payload["label"] == "alt2"
        assert payload["run_id"] == "run-x"
        assert payload["step_id"] == "predict-ticker"
        assert payload["item"] == "AAPL"
        assert payload["attempt"] == 1
        assert payload["session_log_path"] == str(tmp_path / "session.jsonl")
        assert payload["slot_dir"] == str(tmp_path / "slot")

    def test_policy_recorded(self, tmp_path):
        lease = self._lease_with_keys(tmp_path)
        payload = build_auth_lease_acquired(lease, policy="round-robin")
        assert payload["policy"] == "round-robin"

    def test_policy_defaults_empty(self, tmp_path):
        # Empty policy is allowed for the legacy PRIORITY_ORDER path
        # before --auth-policy plumbing lands in the fix.
        payload = build_auth_lease_acquired(self._lease_with_keys(tmp_path))
        assert payload["policy"] == ""

    def test_active_lease_count_snapshot(self, tmp_path):
        lease = self._lease_with_keys(tmp_path)
        snapshot = {"alt1": 12, "alt2": 13}
        payload = build_auth_lease_acquired(lease, active_lease_count=snapshot)

        assert payload["active_lease_count"] == snapshot
        # Defensive copy: caller mutating their dict afterwards must not
        # leak into the recorded payload.
        snapshot["alt2"] = 999
        recorded_counts = cast("dict[str, int]", payload["active_lease_count"])
        assert recorded_counts["alt2"] == 13

    def test_active_lease_count_default_empty_dict(self, tmp_path):
        payload = build_auth_lease_acquired(self._lease_with_keys(tmp_path))
        assert payload["active_lease_count"] == {}

    def test_session_log_path_empty_when_lease_has_none(self, tmp_path):
        # Backwards-compat with leases that pre-date session_log_path
        # threading: legacy probes / tests construct SlotLease without
        # the path, and the event payload should not serialize a literal
        # "None" string.
        lease = SlotLease(
            adapter="claude-code-cli",
            label="alt1",
            slot_dir=tmp_path / "slot",
            holder="legacy",
            scope_env={},
            scrub_env={},
            bootstrap_fp=fingerprint_blob("blob"),
        )
        payload = build_auth_lease_acquired(lease)
        assert payload["session_log_path"] == ""


# ── Integration check against _build_prepare_launch seam ────────


class TestBuildPrepareLaunchAcceptsPoolDispatch:
    """Smoke test that the wiring didn't break pool_dispatch=None path.

    Full end-to-end tests against the retry heap live in P2.7.
    """

    def test_pool_dispatch_param_accepted_by_signature(self):

        sig = inspect.signature(_build_prepare_launch)
        assert "pool_dispatch" in sig.parameters
        # Default must be None so non-pool callers see zero behavior change.
        assert sig.parameters["pool_dispatch"].default is None

    def test_run_agent_pool_accepts_pool_dispatch(self):

        sig = inspect.signature(_run_agent_pool)
        assert "pool_dispatch" in sig.parameters
        assert sig.parameters["pool_dispatch"].default is None


# ── Phase 5: retry-abort seam ──────────────────────────────────


class TestAuthForcesAbort:
    """Plan §Phase 5: pool auth severity must short-circuit retry.

    The dispatch layer consults ``_auth_forces_abort`` on the
    classification returned from teardown. A known-bug or expired
    credential must abort instead of re-entering the retry heap.
    """

    def test_none_classification_does_not_force_abort(self):

        # Non-pool dispatch (or success teardown) passes None — the
        # generic retry path is untouched.
        assert _auth_forces_abort(None) is False

    def test_cooling_classification_does_not_force_abort(self):

        # Cooling → RETRY_AFTER_WAIT → still retryable.
        c = AuthFailureClassification(status="cooling", cooling_until_ts=9999)
        assert _auth_forces_abort(c) is False

    def test_unknown_retry_now_does_not_force_abort(self):

        c = AuthFailureClassification(
            status="unknown",
            severity=FailureSeverity.RETRY_NOW,
        )
        assert _auth_forces_abort(c) is False

    def test_expired_classification_forces_abort(self):

        # status=expired alone (no explicit severity) still aborts via
        # default_severity_for_status.
        c = AuthFailureClassification(status="expired", reason="invalid_grant")
        assert _auth_forces_abort(c) is True

    def test_known_bug_classification_forces_abort(self):

        # Classifier set status=unknown but also set the bug signature —
        # effective_severity() yields ABORT and we must not retry.
        c = AuthFailureClassification(
            status="unknown",
            severity=FailureSeverity.ABORT,
            reason="known-bug:retired-packet-yaml-path",
            known_bug_signature="retired-packet-yaml-path",
        )
        assert _auth_forces_abort(c) is True

    def test_explicit_abort_severity_forces_abort(self):

        c = AuthFailureClassification(
            status="unknown",
            severity=FailureSeverity.ABORT,
        )
        assert _auth_forces_abort(c) is True


class TestPoolDispatchKnownBugSkipsRetry:
    """Integration: a pool-dispatched known-bug must neither retry nor
    lose its signature in the recorded AuthOutcome (plan §Phase 5 P5.3).

    Exercises the whole slot-failure-to-outcome chain via the real
    classifier and ``build_auth_outcome``. The combination is the
    operator-facing contract: the retry path consults
    ``effective_severity()`` (verified in ``TestAuthForcesAbort``),
    the outcome stream carries the signature for grep aggregation.
    """

    def _lease(self, tmp_path: Path, adapter: str = "claude-code-cli") -> SlotLease:
        return SlotLease(
            adapter=adapter,
            label="laptop",
            slot_dir=tmp_path / "slot",
            holder="h",
            scope_env={},
            scrub_env={},
            bootstrap_fp=fingerprint_blob("original"),
        )

    def test_known_bug_error_classifies_abort_and_outcome_carries_signature(self, tmp_path: Path):

        lease = self._lease(tmp_path)
        # Use the real ClaudeCodeCliAdapter so this test catches a
        # future regression where the classifier ordering regresses.
        classification = classify_failure_for_slot(
            lease,
            error_str="FileNotFoundError: [Errno 2] No such file: /runs/x/packet.yaml",
        )
        # 1. Retry path short-circuits on ABORT.
        assert _auth_forces_abort(classification) is True
        assert classification.effective_severity() == FailureSeverity.ABORT
        assert classification.known_bug_signature == "retired-packet-yaml-path"

        # 2. Recorded outcome carries the signature AND severity so
        # operators can `jq '.known_bug_signature'` over the event stream.
        outcome = build_auth_outcome(
            lease,
            classification=classification,
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert outcome.known_bug_signature == "retired-packet-yaml-path"
        assert outcome.severity == FailureSeverity.ABORT
        assert outcome.classification == "unknown"  # not a credential fault

    def test_oauth_refresh_failure_classifies_expired_retry_after_wait(self, tmp_path: Path):

        lease = self._lease(tmp_path)
        lease.slot_dir.mkdir(parents=True, exist_ok=True)
        (lease.slot_dir / "claude-code-debug.log").write_text(
            "[DEBUG] [API:auth] OAuth token check starting\n"
            "[ERROR] AxiosError: [url=https://platform.claude.com/v1/oauth/token,"
            " status=400] AxiosError: Request failed with status code 400\n"
        )
        classification = classify_failure_for_slot(
            lease,
            error_str="Process exited with exit code 1",
        )
        assert classification.status == "expired"
        assert classification.oauth_refresh_status == 400
        assert classification.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT
        assert classification.known_bug_signature is None

        outcome = build_auth_outcome(
            lease,
            classification=classification,
            flushed_blob=None,
            retry_count=0,
            fallback_policy=FallbackPolicy.NONE,
        )
        assert outcome.classification == "expired"
        assert outcome.severity == FailureSeverity.RETRY_AFTER_WAIT
        assert outcome.known_bug_signature == ""


# ── Phase 5: end-to-end teardown → auth_outcome → retry-skip ──


class _RecordingPool:
    """Minimal RunPool stand-in that captures ``record_auth_outcome`` calls.

    The real :class:`metaproc.runpool.pool.RunPool` is async + starts
    threads; all we need here is the event-recording seam so we can
    assert the outcome stream carries severity + known_bug_signature.
    """

    def __init__(self) -> None:
        self.outcomes: list[dict[str, object]] = []

    def record_auth_outcome(self, outcome: dict[str, object]) -> None:
        self.outcomes.append(outcome)


class _RealClassifierStub(_StubAdapter):
    """Stub that defers to the real ClaudeCodeCliAdapter.classify_failure.

    The other ``_StubAdapter`` has a baby classifier; here we want the
    full Phase 5 classifier (known-bug detector + ordering fix) so the
    integration test proves end-to-end that a known-bug error string
    produces a known-bug outcome. Reuses the stub for everything else
    so materialize/capture/flush don't try to touch real Keychain.
    """

    def classify_failure(
        self, exc: BaseException | None, stderr: str, session_log_path: Path | None
    ) -> AuthFailureClassification:

        return ClaudeCodeCliAdapter().classify_failure(exc, stderr, session_log_path)


class TestTeardownRecordsAuthOutcome:
    """Integration: _teardown_pool_slot writes an auth_outcome for
    every leased attempt, with severity + known_bug_signature.

    Covers the plan §Phase 5 aggregation surface: operators grep the
    run's event stream for ``"event":"auth_outcome"`` + a signature
    rather than reading the credential backend.
    """

    def _seeded_coordinator(self, tmp_path: Path):
        pool_state = _InMemoryPool()
        pool_state.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp=fingerprint_blob("laptop-blob")),
            blob="laptop-blob",
        )
        adapter = _RealClassifierStub()
        coord = SlotCoordinator(pool_state, adapter_registry={"claude-code-cli": adapter})
        return pool_state, adapter, coord, tmp_path

    def _acquire(self, adapter, coord: SlotCoordinator, tmp_path: Path, *, monkeypatch):

        monkeypatch.setattr(
            pd,
            "get_auth_capable",
            lambda name: adapter if name == "claude-code-cli" else None,
        )
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r1",
            step="predict",
            fallback_policy=FallbackPolicy.SAME_PROVIDER,
        )
        lease = acquire_slot(config, item="AAPL", attempt=0)
        return config, lease

    def test_known_bug_error_records_outcome_and_returns_abort_classification(
        self, tmp_path: Path, monkeypatch
    ):

        _pool_state, adapter, coord, tmp_path = self._seeded_coordinator(tmp_path)
        config, lease = self._acquire(adapter, coord, tmp_path, monkeypatch=monkeypatch)
        recorder = _RecordingPool()
        shared: dict[str, Any] = {"slot_lease": lease, "attempt_number": 1, "log_path": None}

        classification = _teardown_pool_slot(
            pool_dispatch=config,
            pool=recorder,
            shared=shared,
            error_str="FileNotFoundError: [Errno 2] No such file: /runs/x/v11/packet.yaml",
        )

        # 1. classification reflects the known-bug → ABORT verdict so
        #    the retry path skips the heap.
        assert classification is not None
        assert classification.known_bug_signature == "retired-packet-yaml-path"
        assert _auth_forces_abort(classification) is True
        assert classification.effective_severity() == FailureSeverity.ABORT

        # 2. Exactly one outcome recorded, carrying the signature +
        #    ABORT severity + retry_count=0 (first attempt).
        assert len(recorder.outcomes) == 1
        outcome = recorder.outcomes[0]
        assert outcome["known_bug_signature"] == "retired-packet-yaml-path"
        assert outcome["severity"] == FailureSeverity.ABORT
        assert outcome["classification"] == "unknown"  # not a credential fault
        assert outcome["retry_count"] == 0
        assert outcome["fallback_policy"] == str(FallbackPolicy.SAME_PROVIDER)
        assert outcome["adapter"] == "claude-code-cli"
        assert outcome["label"] == "laptop"

        # 3. Slot lease was popped so a second teardown is a no-op
        #    (idempotency guarantee).
        assert "slot_lease" not in shared
        assert (
            _teardown_pool_slot(
                pool_dispatch=config,
                pool=recorder,
                shared=shared,
                error_str="anything",
            )
            is None
        )
        assert len(recorder.outcomes) == 1  # no second event

    def test_oauth_refresh_failure_records_expired_retry_outcome(self, tmp_path: Path, monkeypatch):

        _pool_state, adapter, coord, tmp_path = self._seeded_coordinator(tmp_path)
        config, lease = self._acquire(adapter, coord, tmp_path, monkeypatch=monkeypatch)
        # Plant the structured OAuth refresh failure in the slot debug log.
        (lease.slot_dir / "claude-code-debug.log").write_text(
            "[ERROR] AxiosError: [url=https://platform.claude.com/v1/oauth/token,"
            " status=400] AxiosError: Request failed with status code 400\n"
        )
        recorder = _RecordingPool()
        shared: dict[str, Any] = {"slot_lease": lease, "attempt_number": 2}

        classification = _teardown_pool_slot(
            pool_dispatch=config,
            pool=recorder,
            shared=shared,
            error_str="Process exited with exit code 1",
        )

        assert classification is not None
        assert classification.status == "expired"
        assert classification.known_bug_signature is None
        assert classification.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT

        assert len(recorder.outcomes) == 1
        outcome = recorder.outcomes[0]
        assert outcome["classification"] == "expired"
        assert outcome["severity"] == FailureSeverity.RETRY_AFTER_WAIT
        assert outcome["known_bug_signature"] == ""
        # retry_count = attempt_number - 1, so the second attempt's
        # outcome reports retry_count=1 (one prior retry burned).
        assert outcome["retry_count"] == 1
        assert outcome["adapter"] == "claude-code-cli"
        # The failed (adapter, label) was pushed to pool_exclude so
        # the next retry's selector walks forward past it (P2.6).
        assert shared["pool_exclude"] == [("claude-code-cli", "laptop")]

    def test_success_teardown_records_ok_outcome(self, tmp_path: Path, monkeypatch):

        _pool_state, adapter, coord, tmp_path = self._seeded_coordinator(tmp_path)
        config, lease = self._acquire(adapter, coord, tmp_path, monkeypatch=monkeypatch)
        recorder = _RecordingPool()
        shared: dict[str, Any] = {"slot_lease": lease, "attempt_number": 1}

        classification = _teardown_pool_slot(
            pool_dispatch=config,
            pool=recorder,
            shared=shared,
            error_str=None,
        )
        assert classification is None  # no failure classification on success
        assert len(recorder.outcomes) == 1
        outcome = recorder.outcomes[0]
        assert outcome["classification"] == "ok"
        assert outcome["severity"] == ""  # success → no severity verdict
        assert outcome["known_bug_signature"] == ""
        assert outcome["retry_count"] == 0

    def test_teardown_preserves_claude_code_debug_log_to_session_logs_dir(
        self, tmp_path: Path, monkeypatch
    ):

        _pool_state, adapter, coord, tmp_path = self._seeded_coordinator(tmp_path)
        config, lease = self._acquire(adapter, coord, tmp_path, monkeypatch=monkeypatch)

        # Simulate the worker writing a debug log into the slot.
        debug_content = (
            "2026-04-27T18:08:18Z [DEBUG] [API:auth] OAuth token check starting\n"
            "2026-04-27T18:08:19Z [ERROR] API error (attempt 1/11): 401 ...\n"
        )
        (lease.slot_dir / "claude-code-debug.log").write_text(debug_content)

        # Real-shape session log path under .logs/ (this is what
        # prepare_step / runtime.py builds for live runs).
        logs_dir = tmp_path / "predict-run" / "predict" / ".logs"
        session_log = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.jsonl"
        recorder = _RecordingPool()
        shared: dict[str, Any] = {
            "slot_lease": lease,
            "attempt_number": 1,
            "log_path": session_log,
        }

        _teardown_pool_slot(
            pool_dispatch=config,
            pool=recorder,
            shared=shared,
            error_str="exit code 1",  # failure path
        )

        # Slot dir was wiped (teardown's job).
        assert not lease.slot_dir.exists()
        # And the debug log lives next to where the session log would
        # be, with the session-log stem prefix so it sorts adjacent.
        preserved = logs_dir / "predict-ticker_HLT_2026-04-27T18-07-20.claude-code-debug.log"
        assert preserved.exists(), "claude-code-debug.log must be preserved next to session log"
        assert preserved.read_text() == debug_content

    def test_no_pool_dispatch_is_noop(self, tmp_path: Path):

        recorder = _RecordingPool()
        shared: dict[str, Any] = {"attempt_number": 1}

        classification = _teardown_pool_slot(
            pool_dispatch=None,
            pool=recorder,
            shared=shared,
            error_str="anything",
        )
        assert classification is None
        assert recorder.outcomes == []


class TestEventLoggerAuthOutcome:
    """The JSONL event logger must expose an ``auth_outcome`` event."""

    def test_auth_outcome_event_written_as_jsonl(self, tmp_path: Path):

        events_file = tmp_path / "events.jsonl"
        logger = EventLogger(events_file)
        logger.open()
        try:
            logger.auth_outcome(
                {
                    "schema_version": 1,
                    "adapter": "claude-code-cli",
                    "label": "laptop",
                    "classification": "unknown",
                    "severity": "abort",
                    "known_bug_signature": "retired-packet-yaml-path",
                    "retry_count": 0,
                    "fallback_policy": "same-provider",
                }
            )
        finally:
            logger.close()

        lines = events_file.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "auth_outcome"
        assert record["known_bug_signature"] == "retired-packet-yaml-path"
        assert record["severity"] == "abort"
        assert "ts" in record  # EventLogger stamps ts on every event


class TestComputePoolCoolingDelay:
    """Regression coverage: cooling-aware retry delay for pool-exhausted items."""

    def _make_dispatch(self, entries: list[PoolEntry]) -> PoolDispatchConfig:

        pool = _InMemoryPool()
        for e in entries:
            pool.entries[(e.adapter, e.label)] = e
        coord = SlotCoordinator(pool)
        return PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=Path("/tmp"),
            run_id="r",
            step="s",
        )

    def test_no_pool_dispatch_returns_ceiling(self):

        d = _compute_pool_cooling_delay(None, ceiling_s=600)
        assert d == 600

    def test_no_cooling_entries_returns_contention_delay(self):

        # All active, none cooling — this is lease contention (labels
        # healthy, just all leased by other items). Return contention_s
        # so the next retry grabs a freed slot quickly. Surfaced by the
        # 2026-04-28 smoke at mine-adhoc.
        dispatch = self._make_dispatch(
            [
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt1",
                    blob="b",
                    state=EntryState(status="active", fp="fp1"),
                    etag="et",
                ),
            ]
        )
        d = _compute_pool_cooling_delay(dispatch, ceiling_s=600, contention_s=45)
        assert d == 45

    def test_picks_earliest_cooling_until(self):

        now = _time.time()
        dispatch = self._make_dispatch(
            [
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt1",
                    blob="b",
                    state=EntryState(status="cooling", fp="fp1", cooling_until_ts=int(now + 200)),
                    etag="et1",
                ),
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt2",
                    blob="b",
                    state=EntryState(status="cooling", fp="fp2", cooling_until_ts=int(now + 800)),
                    etag="et2",
                ),
            ]
        )
        # Expect ~200s + jitter (default 30) — capped by ceiling 1800.
        d = _compute_pool_cooling_delay(dispatch, floor_s=1.0, ceiling_s=1800.0, jitter_s=30.0)
        assert 200 <= d <= 260, f"expected ~230s, got {d}"

    def test_floor_clamps_minimum(self):

        now = _time.time()
        dispatch = self._make_dispatch(
            [
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt1",
                    blob="b",
                    state=EntryState(status="cooling", fp="fp1", cooling_until_ts=int(now)),
                    etag="et",
                ),
            ]
        )
        # cooling_until is now → wait_s = jitter, but floor=60 raises it.
        d = _compute_pool_cooling_delay(dispatch, floor_s=60.0, ceiling_s=600.0, jitter_s=10.0)
        assert d == 60.0

    def test_ceiling_caps_maximum(self):

        now = _time.time()
        dispatch = self._make_dispatch(
            [
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt1",
                    blob="b",
                    state=EntryState(
                        status="cooling", fp="fp1", cooling_until_ts=int(now + 100_000)
                    ),
                    etag="et",
                ),
            ]
        )
        # Far-future cooling → capped at ceiling.
        d = _compute_pool_cooling_delay(dispatch, ceiling_s=300.0)
        assert d == 300.0

    def test_cooling_without_ts_uses_ceiling_not_contention(self):

        dispatch = self._make_dispatch(
            [
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt1",
                    blob="b",
                    state=EntryState(status="cooling", fp="fp1", cooling_until_ts=None),
                    etag="et1",
                ),
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt2",
                    blob="b",
                    state=EntryState(status="cooling", fp="fp2", cooling_until_ts=None),
                    etag="et2",
                ),
            ]
        )
        d = _compute_pool_cooling_delay(dispatch, ceiling_s=1800.0, contention_s=60.0)
        assert d == 1800.0

    def test_active_alongside_cooling_no_ts_still_uses_ceiling(self):

        dispatch = self._make_dispatch(
            [
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt1",
                    blob="b",
                    state=EntryState(status="cooling", fp="fp1", cooling_until_ts=None),
                    etag="et1",
                ),
                PoolEntry(
                    adapter="claude-code-cli",
                    label="alt2",
                    blob="b",
                    state=EntryState(status="active", fp="fp2"),
                    etag="et2",
                ),
            ]
        )
        d = _compute_pool_cooling_delay(dispatch, ceiling_s=900.0, contention_s=60.0)
        assert d == 900.0


class TestPreFanOutProbe:
    """Pre-flight probe gate: probe each label, filter expired/cooling
    out of the strategy before items dispatch.
    """

    def _config_with_strategy(self, tmp_path, *, labels: tuple[str, ...]):

        pool = _InMemoryPool()
        for label in labels:
            pool.seed(
                "claude-code-cli",
                label,
                state=EntryState(status="active", fp=f"fp-{label}"),
                blob=f"blob-{label}",
            )
        adapter = _StubAdapter()
        coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": adapter})
        config = PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, labels),
            fallback_policy=FallbackPolicy.NONE,
        )
        return config, pool, adapter

    def _patch_probe_results(self, monkeypatch, results: dict[str, str]):
        """Stub probe_credential so each label resolves to a target_status.

        ``results`` maps label → target_status ("active" | "expired" |
        "cooling" | "unknown"). Probe metadata (signals, exit code,
        notes) populated minimally; tests don't assert against them.
        """

        def fake_probe(
            adapter_impl, blob, *, adapter_name, vehicle=None, timeout_s=60, sidecar_target=None
        ):
            # blob is "blob-<label>" per _config_with_strategy.
            label = blob.split("-", 1)[1]
            target = results.get(label, "active")
            signals = ClaudeApiSignals(
                api_status=401 if target == "expired" else None,
                oauth_refresh_status=400 if target == "expired" else None,
            )
            return pd.CredentialProbeResult(
                signals=signals,
                target_status=target,
                note=f"stub probe → {target}",
                exit_code=0 if target == "active" else 1,
                stdout="",
                debug_log="",
            )

        monkeypatch.setattr(pd, "probe_credential", fake_probe)
        monkeypatch.setattr(
            "metaproc.adapters.registry.get_auth_capable",
            lambda name: _StubAdapter() if name == "claude-code-cli" else None,
        )

    def test_all_active_passes_strategy_through(self, tmp_path, monkeypatch):

        config, pool, _ = self._config_with_strategy(tmp_path, labels=("alt1", "alt2"))
        self._patch_probe_results(monkeypatch, {"alt1": "active", "alt2": "active"})
        new_config = pre_fan_out_probe(config, pool)
        assert new_config.strategy.labels == ("alt1", "alt2")
        # Pool entries unchanged (no transitions on active).
        assert pool.entries[("claude-code-cli", "alt1")].state.status == "active"
        assert pool.entries[("claude-code-cli", "alt2")].state.status == "active"

    def test_expired_label_removed_and_marked(self, tmp_path, monkeypatch):

        config, pool, _ = self._config_with_strategy(tmp_path, labels=("alt1", "alt2"))
        self._patch_probe_results(monkeypatch, {"alt1": "active", "alt2": "expired"})
        new_config = pre_fan_out_probe(config, pool)
        assert new_config.strategy.labels == ("alt1",)
        # Pool entry for alt2 transitioned to expired so the rest of
        # the run treats it as ineligible.
        assert pool.entries[("claude-code-cli", "alt2")].state.status == "expired"

    def test_cooling_label_removed_and_marked(self, tmp_path, monkeypatch):

        config, pool, _ = self._config_with_strategy(tmp_path, labels=("alt1", "alt2"))
        self._patch_probe_results(monkeypatch, {"alt1": "active", "alt2": "cooling"})
        new_config = pre_fan_out_probe(config, pool)
        assert new_config.strategy.labels == ("alt1",)
        assert pool.entries[("claude-code-cli", "alt2")].state.status == "cooling"

    def test_all_labels_fail_raises_pool_unavailable(self, tmp_path, monkeypatch):

        config, pool, _ = self._config_with_strategy(tmp_path, labels=("alt1", "alt2"))
        self._patch_probe_results(monkeypatch, {"alt1": "expired", "alt2": "expired"})
        with pytest.raises(PoolSlotUnavailableError):
            pre_fan_out_probe(config, pool)
        # Both pool entries transitioned to expired even though the
        # whole probe ultimately failed.
        assert pool.entries[("claude-code-cli", "alt1")].state.status == "expired"
        assert pool.entries[("claude-code-cli", "alt2")].state.status == "expired"

    def test_already_expired_label_skipped_without_reprobe(self, tmp_path, monkeypatch):

        config, pool, _ = self._config_with_strategy(tmp_path, labels=("alt1", "alt2"))
        # Pre-mark alt2 as expired in the pool.
        old = pool.entries[("claude-code-cli", "alt2")]
        pool.upsert_entry(
            "claude-code-cli",
            "alt2",
            blob=old.blob,
            state=EntryState(status="expired", fp=old.state.fp),
            expected_etag=old.etag,
        )
        # Probe stub would mark alt1 active, alt2 active — but alt2
        # should be skipped (no probe call) because it's already expired.
        probed_labels: list[str] = []

        def tracking_probe(
            adapter_impl, blob, *, adapter_name, vehicle=None, timeout_s=60, sidecar_target=None
        ):
            label = blob.split("-", 1)[1]
            probed_labels.append(label)
            return pd.CredentialProbeResult(
                signals=ClaudeApiSignals(),
                target_status="active",
                note="",
                exit_code=0,
                stdout="",
                debug_log="",
            )

        monkeypatch.setattr(pd, "probe_credential", tracking_probe)
        monkeypatch.setattr(
            "metaproc.adapters.registry.get_auth_capable",
            lambda name: _StubAdapter() if name == "claude-code-cli" else None,
        )
        new_config = pre_fan_out_probe(config, pool)
        assert probed_labels == ["alt1"]  # alt2 skipped
        assert new_config.strategy.labels == ("alt1",)

    def test_skips_with_warning_for_non_claude_adapter(self, tmp_path, monkeypatch, caplog):

        config, pool, _ = self._config_with_strategy(tmp_path, labels=("alt1",))
        config = replace(config, adapter="codex-cli")
        # codex-cli must register as auth-capable so the prior guard doesn't
        # short-circuit; the new claude-only guard is the one we're testing.
        monkeypatch.setattr(
            "metaproc.adapters.registry.get_auth_capable",
            lambda _name: _StubAdapter(),
        )

        with caplog.at_level(logging.WARNING, logger="metaproc.dispatch.pool_dispatch"):
            new_config = pre_fan_out_probe(config, pool)

        # Strategy passes through unchanged — no labels filtered out.
        assert new_config.strategy.labels == config.strategy.labels
        assert any("Claude-only" in rec.message for rec in caplog.records), (
            "expected a warning naming the Claude-only limitation"
        )

    def test_probe_credential_raises_for_non_claude(self):

        with pytest.raises(NotImplementedError, match="generic credential probing"):
            probe_credential(_StubAdapter(), "blob", adapter_name="codex-cli")

    def test_pre_fan_out_probe_threads_vehicle_through(self, tmp_path, monkeypatch):

        pool = _InMemoryPool()
        pool.seed(
            "claude-code-cli",
            "vehicle-a",
            state=EntryState(status="active", fp="fp-a", vehicle=_Vehicle.OAUTH_TOKEN),
            blob="oauth-token-blob",
        )
        pool.seed(
            "claude-code-cli",
            "vehicle-b",
            state=EntryState(status="active", fp="fp-b", vehicle=_Vehicle.LOGIN_CREDENTIALS),
            blob='{"claudeAiOauth":{}}',
        )
        adapter = _StubAdapter()
        coord = SlotCoordinator(pool, adapter_registry={"claude-code-cli": adapter})
        config = pd.PoolDispatchConfig(
            coordinator=coord,
            adapter="claude-code-cli",
            runs_dir=tmp_path,
            run_id="r",
            step="",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("vehicle-a", "vehicle-b")),
            fallback_policy=FallbackPolicy.NONE,
        )

        seen: dict[str, _Vehicle] = {}

        def capture(
            adapter_impl, blob, *, adapter_name, vehicle, timeout_s=60, sidecar_target=None
        ):
            seen[blob] = vehicle
            return pd.CredentialProbeResult(
                signals=ClaudeApiSignals(),
                target_status="active",
                note="ok",
                exit_code=0,
                stdout="",
                debug_log="",
            )

        monkeypatch.setattr(pd, "probe_credential", capture)
        monkeypatch.setattr(
            "metaproc.adapters.registry.get_auth_capable",
            lambda name: adapter if name == "claude-code-cli" else None,
        )
        pd.pre_fan_out_probe(config, pool)
        assert seen["oauth-token-blob"] == _Vehicle.OAUTH_TOKEN
        assert seen['{"claudeAiOauth":{}}'] == _Vehicle.LOGIN_CREDENTIALS
