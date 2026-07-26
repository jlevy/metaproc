"""Tests for metaproc.dispatch.credential_pool.

Covers label-safe codec, state helpers, eligibility, and the
GcpSecretManagerBackend happy path + CAS + idempotent delete against
a fake Secret Manager SDK client. No live GCP.

Plan: plan-2026-04-21-auth-credential-pool.md (P1.3 + part of P1.5).
"""

from __future__ import annotations

import subprocess
import time as _time
from dataclasses import dataclass, field
from typing import Any

import pytest

from metaproc.dispatch.credential_pool import (
    ActiveLeaseCounter,
    AtomicCounter,
    ConcurrentModificationError,
    EntryState,
    FallbackPolicy,
    GcpSecretManagerBackend,
    PoolEntry,
    SelectionPolicy,
    SelectionStrategy,
    _GcpSdkAdapter,
    decode_labels,
    eligible_labels,
    encode_labels,
    fingerprint_blob,
    mark_cooling,
    mark_expired,
    mark_ok,
    secret_name_for,
    select_credential,
    select_fallback,
    state_mark_cooling,
    state_mark_expired,
    state_mark_ok,
    validate_operator_label,
    write_back_rotated,
)

# ── Label codec ─────────────────────────────────────────────────


class TestOperatorLabelValidation:
    def test_accepts_simple_tags(self):
        for tag in ("laptop", "home", "alt1", "work-mac", "a"):
            validate_operator_label(tag)

    def test_accepts_forty_char_max(self):
        validate_operator_label("a" * 40)

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match=r"\[a-z0-9-\]"):
            validate_operator_label("")

    def test_rejects_uppercase(self):
        with pytest.raises(ValueError):
            validate_operator_label("Laptop")

    def test_rejects_underscore(self):
        # Reserved for internal label keys; operator tags are narrower.
        with pytest.raises(ValueError):
            validate_operator_label("laptop_macbook")

    def test_rejects_special_chars(self):
        for tag in ("laptop/2", "work:mac", "space here", "emoji🤖"):
            with pytest.raises(ValueError):
                validate_operator_label(tag)

    def test_rejects_over_forty(self):
        with pytest.raises(ValueError):
            validate_operator_label("a" * 41)


class TestSecretNameFor:
    def test_claude_format(self):
        assert (
            secret_name_for("claude-code-cli", "levy", "laptop") == "claude-code-auth-levy-laptop"
        )

    def test_codex_format(self):
        # Codex follows the same "<adapter-short>-auth-<user>-<label>" rule.
        assert secret_name_for("codex-cli", "levy", "work") == "codex-auth-levy-work"

    def test_rejects_invalid_label(self):
        with pytest.raises(ValueError):
            secret_name_for("claude-code-cli", "levy", "BAD")


class TestLabelCodecRoundtrip:
    def test_full_round_trip(self):
        state = EntryState(
            status="cooling",
            fp="abc123def456",
            last_ok_ts=1700,
            last_quota_ts=1710,
            cooling_until_ts=1800,
            expired_ts=None,
        )
        labels = encode_labels(
            state, adapter="claude-code-cli", pool_user="levy", operator_label="laptop"
        )
        decoded = decode_labels(labels)
        assert decoded.status == "cooling"
        assert decoded.fp == "abc123def456"
        assert decoded.cooling_until_ts == 1800
        assert decoded.last_ok_ts == 1700
        assert decoded.last_quota_ts == 1710

    def test_none_timestamps_roundtrip_as_empty(self):
        state = EntryState(status="active", fp="aaa")
        labels = encode_labels(state, adapter="claude-code-cli", pool_user="u", operator_label="l")
        assert labels["cooling_until_ts"] == ""
        assert labels["last_ok_ts"] == ""
        assert labels["expired_ts"] == ""
        decoded = decode_labels(labels)
        assert decoded.cooling_until_ts is None

    def test_encode_rejects_invalid_operator_label(self):
        state = EntryState(status="active", fp="aaa")
        with pytest.raises(ValueError):
            encode_labels(state, adapter="claude-code-cli", pool_user="levy", operator_label="BAD")

    def test_encode_rejects_unsafe_pool_user(self):
        # Long/invalid pool_user bubbles up through the _LABEL_VALUE_RE
        # guard; the encoder catches it before a confusing SDK error.
        state = EntryState(status="active", fp="aaa")
        with pytest.raises(ValueError, match="violates"):
            encode_labels(
                state, adapter="claude-code-cli", pool_user="Bad User", operator_label="laptop"
            )

    def test_decode_unknown_status_is_disabled(self):
        decoded = decode_labels(
            {
                "adapter": "x",
                "pool_user": "u",
                "label": "l",
                "status": "corrupted",
                "fp": "a",
                "last_ok_ts": "",
                "last_quota_ts": "",
                "cooling_until_ts": "",
                "expired_ts": "",
            }
        )
        # Refuse to select from a corrupt-labeled entry; treat as disabled.
        assert decoded.status == "disabled"


# ── State helpers ────────────────────────────────────────────────


class TestStateHelpers:
    def test_mark_ok_flips_status_and_ts(self):
        s = EntryState(status="cooling", fp="old", last_ok_ts=1, cooling_until_ts=100)
        result = state_mark_ok(s, new_fp="new")
        assert result.status == "active"
        assert result.fp == "new"
        assert result.last_ok_ts is not None
        assert result.last_ok_ts > 1
        # Write-back does not clear the cooling timestamp — next probe can
        # still see what the label came out of. Only status flips.
        assert result.cooling_until_ts == 100

    def test_mark_ok_without_fp_keeps_existing(self):
        s = EntryState(status="active", fp="keep-me", last_ok_ts=1)
        result = state_mark_ok(s)
        assert result.fp == "keep-me"

    def test_mark_cooling_flips_status_and_quota_ts(self):
        s = EntryState(status="active", fp="a")
        result = state_mark_cooling(s, cooling_until_ts=42)
        assert result.status == "cooling"
        assert result.cooling_until_ts == 42
        assert result.last_quota_ts is not None

    def test_mark_cooling_indefinite(self):
        s = EntryState(status="active", fp="a")
        result = state_mark_cooling(s, cooling_until_ts=None)
        assert result.status == "cooling"
        assert result.cooling_until_ts is None

    def test_mark_expired_flips_status_only(self):
        s = EntryState(status="active", fp="a")
        result = state_mark_expired(s)
        assert result.status == "expired"
        assert result.expired_ts is not None


class TestEligibility:
    def _entry(self, label: str, state: EntryState) -> PoolEntry:
        return PoolEntry(
            adapter="claude-code-cli",
            label=label,
            blob="{}",
            state=state,
            etag="e",
        )

    def test_active_is_eligible(self):
        entries = [self._entry("a", EntryState(status="active", fp="a"))]
        assert [e.label for e in eligible_labels(entries, now=10)] == ["a"]

    def test_cooling_until_past_is_eligible(self):
        entries = [self._entry("a", EntryState(status="cooling", fp="a", cooling_until_ts=5))]
        assert [e.label for e in eligible_labels(entries, now=10)] == ["a"]

    def test_cooling_until_future_is_not_eligible(self):
        entries = [self._entry("a", EntryState(status="cooling", fp="a", cooling_until_ts=100))]
        assert list(eligible_labels(entries, now=10)) == []

    def test_cooling_indefinitely_is_not_eligible(self):
        # cooling_until_ts=None means "until probe/operator" — never auto-eligible.
        entries = [self._entry("a", EntryState(status="cooling", fp="a", cooling_until_ts=None))]
        assert list(eligible_labels(entries, now=10)) == []

    def test_expired_is_not_eligible(self):
        entries = [self._entry("a", EntryState(status="expired", fp="a"))]
        assert list(eligible_labels(entries, now=10)) == []

    def test_disabled_is_not_eligible(self):
        entries = [self._entry("a", EntryState(status="disabled", fp="a"))]
        assert list(eligible_labels(entries, now=10)) == []


# ── GCP SDK fake + backend ───────────────────────────────────────


@dataclass
class _FakeVersion:
    name: str
    payload_data: bytes

    @property
    def payload(self):  # noqa: ANN201
        return _FakePayload(self.payload_data)


@dataclass
class _FakePayload:
    data: bytes


@dataclass
class _FakeSecret:
    name: str
    labels: dict[str, str] = field(default_factory=dict)
    etag: str = "etag-0"
    versions: list[_FakeVersion] = field(default_factory=list)


class _FakeSecretManagerClient:
    """Minimal fake mirroring the SDK surface we use.

    Any method we haven't wired raises so tests that accidentally
    reach for a non-pool code path fail loudly instead of leaking.
    """

    def __init__(self) -> None:
        self.secrets: dict[str, _FakeSecret] = {}
        self._etag_counter = 0
        self.calls: list[str] = []
        self.subprocess_calls: list[tuple[str, ...]] = []

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    # get_secret
    def get_secret(self, *, name: str) -> _FakeSecret:
        self.calls.append(f"get_secret:{name}")
        if name not in self.secrets:
            msg = f"NotFound: {name}"
            raise RuntimeError(msg)
        return self.secrets[name]

    # list_secrets — supports a tiny filter grammar.
    def list_secrets(self, *, request: dict[str, Any]):  # noqa: ANN201
        self.calls.append(f"list_secrets:{request['parent']}:{request.get('filter', '')}")
        flt = request.get("filter", "")
        required: dict[str, str] = {}
        for part in flt.split(" AND "):
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.removeprefix("labels.").strip()
                required[k] = v.strip()
        for secret in self.secrets.values():
            if all(secret.labels.get(k) == v for k, v in required.items()):
                yield secret

    # access_secret_version
    def access_secret_version(self, *, name: str) -> _FakeVersion:
        self.calls.append(f"access:{name}")
        # name format: projects/<p>/secrets/<s>/versions/<v>
        parts = name.split("/versions/")
        secret_name = parts[0]
        if secret_name not in self.secrets:
            msg = f"NotFound: {name}"
            raise RuntimeError(msg)
        secret = self.secrets[secret_name]
        if not secret.versions:
            msg = f"no versions for {secret_name}"
            raise RuntimeError(msg)
        # 'latest' returns most recent; numeric returns that index+1.
        if parts[1] == "latest":
            return secret.versions[-1]
        idx = int(parts[1]) - 1
        return secret.versions[idx]

    # create_secret
    def create_secret(self, *, parent: str, secret_id: str, secret: dict[str, Any]) -> _FakeSecret:
        full_name = f"{parent}/secrets/{secret_id}"
        self.calls.append(f"create:{full_name}")
        if full_name in self.secrets:
            msg = f"AlreadyExists: {full_name}"
            raise RuntimeError(msg)
        labels_raw: Any = secret.get("labels", {})
        labels: dict[str, str] = dict(labels_raw) if isinstance(labels_raw, dict) else {}
        obj = _FakeSecret(name=full_name, labels=labels, etag=self._next_etag())
        self.secrets[full_name] = obj
        return obj

    # update_secret
    def update_secret(self, *, secret: dict[str, Any], update_mask: dict[str, Any]) -> _FakeSecret:
        name = secret["name"]
        self.calls.append(f"update:{name}")
        existing = self.secrets.get(name)
        if existing is None:
            msg = f"NotFound: {name}"
            raise RuntimeError(msg)
        if secret.get("etag") != existing.etag:
            # Mirror SDK semantics — FailedPrecondition on stale etag.
            msg = f"FailedPrecondition: stale etag {secret.get('etag')} vs {existing.etag}"
            raise RuntimeError(msg)
        paths = set(update_mask.get("paths") or [])
        if "labels" in paths:
            labels_raw: Any = secret.get("labels", {})
            labels: dict[str, str] = dict(labels_raw) if isinstance(labels_raw, dict) else {}
            existing.labels = labels
        existing.etag = self._next_etag()
        return existing

    # add_secret_version
    def add_secret_version(self, *, parent: str, payload: dict[str, Any]) -> _FakeVersion:
        self.calls.append(f"add_version:{parent}")
        secret = self.secrets.get(parent)
        if secret is None:
            msg = f"NotFound: {parent}"
            raise RuntimeError(msg)
        v = _FakeVersion(
            name=f"{parent}/versions/{len(secret.versions) + 1}",
            payload_data=payload["data"],
        )
        secret.versions.append(v)
        return v

    # delete_secret
    def delete_secret(self, *, name: str) -> None:
        self.calls.append(f"delete:{name}")
        if name not in self.secrets:
            msg = f"NotFound: {name}"
            raise RuntimeError(msg)
        del self.secrets[name]


@pytest.fixture
def fake_backend():
    client = _FakeSecretManagerClient()
    sdk = _GcpSdkAdapter(client=client, project_id="test-proj")
    return GcpSecretManagerBackend(sdk, pool_user="tester"), client


class TestGcpSecretManagerBackend:
    def test_create_entry_writes_secret_labels_and_version(self, fake_backend):
        backend, client = fake_backend
        state = EntryState(status="active", fp="fp1", last_ok_ts=100)
        new_etag = backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob='{"claudeAiOauth":{"accessToken":"x"}}',
            state=state,
        )
        assert new_etag.startswith("etag-")
        full = "projects/test-proj/secrets/claude-code-auth-tester-laptop"
        assert full in client.secrets
        assert client.secrets[full].labels["status"] == "active"
        assert client.secrets[full].labels["fp"] == "fp1"
        # NEVER write pool_user/operator label as "Bad Case" or holder verbatim.
        assert client.secrets[full].labels["pool_user"] == "tester"
        assert len(client.secrets[full].versions) == 1

    def test_get_entry_reads_latest_version_and_labels(self, fake_backend):
        backend, _ = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="blob-v1",
            state=EntryState(status="active", fp="fp1"),
        )
        entry = backend.get_entry("claude-code-cli", "laptop")
        assert entry.blob == "blob-v1"
        assert entry.state.status == "active"
        assert entry.state.fp == "fp1"
        assert entry.etag.startswith("etag-")

    def test_list_entries_filters_by_adapter(self, fake_backend):
        backend, _ = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="a",
            state=EntryState(status="active", fp="a"),
        )
        backend.upsert_entry(
            "claude-code-cli",
            "home",
            blob="b",
            state=EntryState(status="cooling", fp="b", cooling_until_ts=999),
        )
        entries = backend.list_entries(adapter="claude-code-cli")
        labels_seen = sorted(e.label for e in entries)
        assert labels_seen == ["home", "laptop"]

    def test_list_entries_across_adapters(self, fake_backend):
        backend, _ = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="a",
            state=EntryState(status="active", fp="a"),
        )
        # Simulate codex entry landing via a different adapter name.
        backend.upsert_entry(
            "codex-cli",
            "work",
            blob="c",
            state=EntryState(status="active", fp="c"),
        )
        entries = backend.list_entries()
        adapters = sorted({e.adapter for e in entries})
        assert adapters == ["claude-code-cli", "codex-cli"]

    def test_add_version_only_without_state_change(self, fake_backend):
        backend, client = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v1",
            state=EntryState(status="active", fp="fp1"),
        )
        # Second push without state change — only a new version.
        backend.upsert_entry("claude-code-cli", "laptop", blob="v2", state=None)
        full = "projects/test-proj/secrets/claude-code-auth-tester-laptop"
        assert len(client.secrets[full].versions) == 2
        # Labels unchanged.
        assert client.secrets[full].labels["fp"] == "fp1"

    def test_state_only_update(self, fake_backend):
        backend, client = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v1",
            state=EntryState(status="active", fp="fp1"),
        )
        # Pool coordinator marks cooling without adding a version.
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob=None,
            state=EntryState(status="cooling", fp="fp1", cooling_until_ts=2000),
        )
        full = "projects/test-proj/secrets/claude-code-auth-tester-laptop"
        assert client.secrets[full].labels["status"] == "cooling"
        # No new version pushed on state-only flip.
        assert len(client.secrets[full].versions) == 1

    def test_cas_etag_stale_raises(self, fake_backend):
        backend, client = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v1",
            state=EntryState(status="active", fp="fp1"),
        )
        # Force a stale etag by reading then flipping the underlying
        # record out of band before the CAS.
        full = "projects/test-proj/secrets/claude-code-auth-tester-laptop"
        stale_etag = "etag-0"
        # Direct client mutation simulates a racing coordinator.
        client.secrets[full].etag = "etag-999"
        with pytest.raises(RuntimeError, match="FailedPrecondition"):
            # Providing the stale etag triggers the SDK-level CAS failure.
            backend.upsert_entry(
                "claude-code-cli",
                "laptop",
                blob=None,
                state=EntryState(status="cooling", fp="fp1"),
                expected_etag=stale_etag,
            )

    def test_delete_entry_is_idempotent(self, fake_backend):
        backend, _ = fake_backend
        # Delete a non-existent secret — must not raise.
        backend.delete_entry("claude-code-cli", "ghost")
        # Delete an existing one.
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v",
            state=EntryState(status="active", fp="a"),
        )
        backend.delete_entry("claude-code-cli", "laptop")

    def test_rotate_adds_version_before_updating_labels(self, fake_backend):
        # jlevy senior review 2026-04-24: labels must never advertise
        # a new fp / active state before the matching version exists.
        # Record call order on the fake client and assert the version
        # add happens first for existing-secret rotations.
        backend, client = fake_backend
        # Seed the entry so the next upsert hits the "existing" path.
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v1",
            state=EntryState(status="active", fp="fp1"),
        )
        # Clear the recorded call log, then rotate with both a new
        # blob and new state.
        client.calls.clear()
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v2",
            state=EntryState(status="active", fp="fp2"),
        )
        # Find the indices of the two relevant calls. add_version
        # must precede update (the label flip).
        op_sequence = [c.split(":", 1)[0] for c in client.calls]
        add_idx = op_sequence.index("add_version")
        update_idx = op_sequence.index("update")
        assert add_idx < update_idx, (
            f"add_secret_version must precede update_secret so labels "
            f"never race ahead of the payload; got sequence: {op_sequence}"
        )

    def test_rotate_uses_active_version_pointer_not_latest(self, fake_backend):
        # After a successful rotation, get_entry must resolve the
        # active_version label (not versions/latest) so label and
        # payload always agree. This is the positive case for the
        # pointer scheme jlevy's review required.
        backend, client = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v1",
            state=EntryState(status="active", fp="fp1"),
        )
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v2",
            state=EntryState(status="active", fp="fp2"),
        )
        full = "projects/test-proj/secrets/claude-code-auth-tester-laptop"
        # active_version label now names the second version.
        assert client.secrets[full].labels["active_version"] == "2"
        entry = backend.get_entry("claude-code-cli", "laptop")
        assert entry.blob == "v2"
        assert entry.state.fp == "fp2"

    def test_stale_etag_on_blob_plus_state_rotation_leaves_reader_on_old_version(
        self, fake_backend
    ):
        # Regression for jlevy senior review 2026-04-24 on internal review.
        # When a rotate passes blob AND state with a stale etag, the
        # old code pushed the new version unconditionally and then
        # failed the state CAS — leaving `latest` pointing at the new
        # blob while labels said fp_old. Readers materialized the new
        # blob under the old state. The pointer-based fix makes the
        # label CAS control BOTH the state flip and the payload
        # pointer, so a CAS failure leaves reads on the prior version.
        backend, client = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v1-old",
            state=EntryState(status="active", fp="fp-old"),
        )
        full = "projects/test-proj/secrets/claude-code-auth-tester-laptop"
        # Force a stale expected_etag by bumping the server-side etag
        # out of band (simulates a racing pool coordinator).
        stale_etag = client.secrets[full].etag
        client.secrets[full].etag = "etag-999"

        with pytest.raises(RuntimeError, match="FailedPrecondition"):
            backend.upsert_entry(
                "claude-code-cli",
                "laptop",
                blob="v2-new",
                state=EntryState(status="active", fp="fp-new"),
                expected_etag=stale_etag,
            )

        # The dormant new version DID land on the secret — add_secret_version
        # is not CAS'd (SDK provides no etag param for it) and this is
        # expected. The critical invariant is that the reader-facing
        # state is unchanged: active_version still names the prior
        # version, labels still carry the prior fp/status.
        assert len(client.secrets[full].versions) == 2
        # Put the etag back so get_entry can read (the backend reads
        # by name, not by etag, but the test fake checks nothing here).
        client.secrets[full].etag = stale_etag
        entry = backend.get_entry("claude-code-cli", "laptop")
        assert entry.blob == "v1-old", (
            "reader must still see the prior blob — stale-etag CAS "
            "failure must NOT promote the unclaimed new version"
        )
        assert entry.state.fp == "fp-old"
        assert entry.state.status == "active"
        # active_version still names version 1.
        assert client.secrets[full].labels["active_version"] == "1"

    def test_version_add_failure_leaves_labels_on_old_state(self, fake_backend):
        # Simulate `add_secret_version` failing partway through a
        # rotate. With the new ordering the labels MUST still reflect
        # the prior active state + prior fp, not the attempted new fp.
        # Without the fix, labels would flip to the new fp first and
        # the pool would advertise a credential that doesn't exist
        # at `latest`.
        backend, client = fake_backend
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="v1-old",
            state=EntryState(status="active", fp="fp-old"),
        )
        full = "projects/test-proj/secrets/claude-code-auth-tester-laptop"
        prior_labels = dict(client.secrets[full].labels)

        # Patch add_secret_version to blow up.
        def _boom(**_kwargs):
            msg = "simulated network error during version push"
            raise RuntimeError(msg)

        client.add_secret_version = _boom  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="simulated network error"):
            backend.upsert_entry(
                "claude-code-cli",
                "laptop",
                blob="v2-new",
                state=EntryState(status="active", fp="fp-new"),
            )

        # Labels are still the pre-rotate set — selector sees "fp-old".
        # This is the invariant the pool depends on: labels never lie
        # about what payload the secret carries at `latest`.
        assert client.secrets[full].labels == prior_labels
        assert client.secrets[full].labels["fp"] == "fp-old"
        # And `latest` still returns the old blob.
        version = client.access_secret_version(name=f"{full}/versions/latest")
        assert version.payload.data == b"v1-old"

    def test_never_shells_out_to_gcloud(self, fake_backend):
        # Pin the architectural invariant: no subprocess call on any
        # hot path. The agent container ships without gcloud; a
        # backend that shelled out would break Batch workers.
        backend, _client = fake_backend

        original_run = subprocess.run
        calls: list[list[str]] = []

        def trap(*args, **_kwargs):  # noqa: ANN002
            calls.append(list(args[0]) if args else [])
            return original_run(*args, **_kwargs)

        subprocess.run = trap  # type: ignore[assignment]
        try:
            backend.upsert_entry(
                "claude-code-cli",
                "laptop",
                blob="v",
                state=EntryState(status="active", fp="a"),
            )
            backend.get_entry("claude-code-cli", "laptop")
            backend.list_entries("claude-code-cli")
            backend.delete_entry("claude-code-cli", "laptop")
        finally:
            subprocess.run = original_run  # type: ignore[assignment]

        gcloud_calls = [c for c in calls if c and ("gcloud" in c[0] or c[0].endswith("gcloud"))]
        assert gcloud_calls == []


# ── P2.1: FallbackPolicy + select_* helpers ──────────────────────


@dataclass
class _InMemoryPool:
    """PoolBackend-compatible fake used across P2.1 tests.

    The GCP-backend-specific test_credential_pool fixture is too
    heavy for the selection/lease unit tests — it threads an actual
    etag scheme through every call. This fake uses simple integer
    etags and the same semantics (KeyError on missing entry, CAS
    on expected_etag).
    """

    entries: dict[tuple[str, str], PoolEntry] = field(default_factory=dict)
    counter: int = 0

    def _next_etag(self) -> str:
        self.counter += 1
        return f"et-{self.counter}"

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        if (adapter, label) not in self.entries:
            msg = f"no such entry {adapter}/{label}"
            raise KeyError(msg)
        return self.entries[(adapter, label)]

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        return [e for (a, _l), e in self.entries.items() if adapter is None or a == adapter]

    def upsert_entry(self, adapter, label, *, blob, state, expected_etag=None):
        key = (adapter, label)
        existing = self.entries.get(key)
        if expected_etag is not None and existing is not None and existing.etag != expected_etag:
            msg = f"etag mismatch {expected_etag} != {existing.etag}"
            raise ConcurrentModificationError(msg)
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

    def seed(self, adapter: str, label: str, *, state: EntryState, blob: str = "blob") -> str:
        return self.upsert_entry(adapter, label, blob=blob, state=state)


def _seed_active(pool, adapter, label, *, fp="fp", last_quota_ts=None):
    pool.seed(adapter, label, state=EntryState(status="active", fp=fp, last_quota_ts=last_quota_ts))


def _seed_cooling(pool, adapter, label, *, fp="fp", cooling_until_ts=1):
    pool.seed(
        adapter,
        label,
        state=EntryState(status="cooling", fp=fp, cooling_until_ts=cooling_until_ts),
    )


class TestSelectCredential:
    def test_picks_first_eligible_in_priority_order(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "claude-code-cli", "alt2")
        sel = select_credential(
            pool,
            "claude-code-cli",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1", "alt2")),
        )
        assert sel is not None
        assert sel.label == "alt1"

    def test_falls_over_to_next_when_first_cooling(self):
        pool = _InMemoryPool()
        _seed_cooling(pool, "claude-code-cli", "alt1", cooling_until_ts=9999999999)
        _seed_active(pool, "claude-code-cli", "alt2")
        sel = select_credential(
            pool,
            "claude-code-cli",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1", "alt2")),
            now=1,
        )
        assert sel is not None
        assert sel.label == "alt2"

    def test_returns_none_when_all_in_strategy_are_ineligible(self):
        pool = _InMemoryPool()
        _seed_cooling(pool, "claude-code-cli", "alt1", cooling_until_ts=9999999999)
        _seed_cooling(pool, "claude-code-cli", "alt2", cooling_until_ts=9999999999)
        sel = select_credential(
            pool,
            "claude-code-cli",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1", "alt2")),
            now=1,
        )
        assert sel is None

    def test_raises_on_missing_label(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1")
        with pytest.raises(KeyError, match="no pool entry for claude-code-cli/typo") as excinfo:
            select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("typo",)),
            )
        msg = excinfo.value.args[0]
        assert "alt1" in msg

    def test_empty_labels_considers_all_active_alphabetically(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt2")
        _seed_active(pool, "claude-code-cli", "alt1")
        sel = select_credential(pool, "claude-code-cli", strategy=SelectionStrategy())
        assert sel is not None
        assert sel.label == "alt1"

    def test_excluded_label_is_skipped(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "claude-code-cli", "alt2")
        sel = select_credential(
            pool,
            "claude-code-cli",
            strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1", "alt2")),
            exclude_labels=(("claude-code-cli", "alt1"),),
        )
        assert sel is not None
        assert sel.label == "alt2"

    def test_returns_none_on_empty_pool(self):
        pool = _InMemoryPool()
        sel = select_credential(pool, "claude-code-cli", strategy=SelectionStrategy())
        assert sel is None


class TestRoundRobinPolicy:
    """ROUND_ROBIN selection (plan-2026-05-03 fix for P0-10).

    Distributes acquisitions across eligible labels via an atomic
    counter — decisions made at acquisition time, NOT teardown time,
    so the ramp-up period (when no completion events exist) still
    distributes evenly. This is the test class that, run against the
    pre-2026-05-03 PRIORITY_ORDER default, would have caught the
    239/0/0 alt1-only burn that drove the spec.
    """

    def _build_pool(self, *labels: str) -> _InMemoryPool:
        pool = _InMemoryPool()
        for lbl in labels:
            _seed_active(pool, "claude-code-cli", lbl)
        return pool

    def test_requires_rr_counter(self):
        pool = self._build_pool("alt1", "alt2")
        with pytest.raises(ValueError, match="ROUND_ROBIN requires rr_counter"):
            select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.ROUND_ROBIN, ("alt1", "alt2")),
            )

    def test_alternates_across_two_labels(self):

        pool = self._build_pool("alt1", "alt2")
        rr = AtomicCounter()
        labels = []
        for _ in range(6):
            sel = select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.ROUND_ROBIN, ("alt1", "alt2")),
                rr_counter=rr,
            )
            assert sel is not None
            labels.append(sel.label)
        assert labels == ["alt1", "alt2", "alt1", "alt2", "alt1", "alt2"]

    def test_concurrency_regression_25_acquisitions_2_labels_zero_completions(self):

        pool = self._build_pool("alt1", "alt2")
        rr = AtomicCounter()
        counts: dict[str, int] = {"alt1": 0, "alt2": 0}
        for _ in range(25):
            sel = select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.ROUND_ROBIN, ("alt1", "alt2")),
                rr_counter=rr,
            )
            assert sel is not None
            counts[sel.label] += 1
        # 25 / 2 = 12.5; with strict round-robin, expect 13/12 split.
        # ±10% of 12.5 = ±1.25, so 11-14 inclusive on each label.
        assert 11 <= counts["alt1"] <= 14
        assert 11 <= counts["alt2"] <= 14
        # And every label was actually used — the no-load-balance bug.
        assert counts["alt1"] > 0
        assert counts["alt2"] > 0

    def test_concurrency_regression_50_acquisitions_3_labels_zero_completions(self):

        pool = self._build_pool("alt1", "alt2", "alt3")
        rr = AtomicCounter()
        counts: dict[str, int] = {"alt1": 0, "alt2": 0, "alt3": 0}
        for _ in range(50):
            sel = select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.ROUND_ROBIN, ("alt1", "alt2", "alt3")),
                rr_counter=rr,
            )
            assert sel is not None
            counts[sel.label] += 1
        # 50 / 3 = 16.67; ±10% of 16.67 ≈ ±1.67, so 15-18 inclusive.
        for lbl in ("alt1", "alt2", "alt3"):
            assert 15 <= counts[lbl] <= 18, f"label {lbl} count {counts[lbl]} outside band"

    def test_skips_cooling_label_mid_stream(self):

        pool = self._build_pool("alt1", "alt2", "alt3")
        rr = AtomicCounter()
        # 6 acquisitions, all healthy.
        for _ in range(6):
            select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.ROUND_ROBIN, ("alt1", "alt2", "alt3")),
                rr_counter=rr,
            )
        # Cool alt1.
        _seed_cooling(pool, "claude-code-cli", "alt1", cooling_until_ts=9999999999)
        counts: dict[str, int] = {"alt1": 0, "alt2": 0, "alt3": 0}
        for _ in range(20):
            sel = select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.ROUND_ROBIN, ("alt1", "alt2", "alt3")),
                rr_counter=rr,
                now=1,
            )
            assert sel is not None
            counts[sel.label] += 1
        # alt1 cooling: must be skipped entirely.
        assert counts["alt1"] == 0
        # alt2 + alt3 split the remaining 20.
        assert counts["alt2"] + counts["alt3"] == 20
        assert 8 <= counts["alt2"] <= 12
        assert 8 <= counts["alt3"] <= 12

    def test_excluded_label_skipped_in_round_robin(self):

        pool = self._build_pool("alt1", "alt2", "alt3")
        rr = AtomicCounter()
        labels = []
        for _ in range(6):
            sel = select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.ROUND_ROBIN, ("alt1", "alt2", "alt3")),
                exclude_labels=(("claude-code-cli", "alt2"),),
                rr_counter=rr,
            )
            assert sel is not None
            labels.append(sel.label)
        # alt2 excluded — alternation between alt1 and alt3 only.
        assert "alt2" not in labels
        assert set(labels) == {"alt1", "alt3"}


class TestLeastActivePolicy:
    """LEAST_ACTIVE selection (plan-2026-05-03).

    NGINX-style least-connections — picks the eligible label with the
    fewest currently-active leases. Robust when leases have varying
    duration or retries pile up on one label.
    """

    def _build_pool(self, *labels: str) -> _InMemoryPool:
        pool = _InMemoryPool()
        for lbl in labels:
            _seed_active(pool, "claude-code-cli", lbl)
        return pool

    def test_requires_active_counter(self):
        pool = self._build_pool("alt1", "alt2")
        with pytest.raises(ValueError, match="LEAST_ACTIVE requires active_counter"):
            select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.LEAST_ACTIVE, ("alt1", "alt2")),
            )

    def test_picks_lowest_count(self):

        pool = self._build_pool("alt1", "alt2")
        active = ActiveLeaseCounter()
        # Pre-load alt1 with 5 active leases; alt2 with 2.
        for _ in range(5):
            active.acquire("claude-code-cli", "alt1")
        for _ in range(2):
            active.acquire("claude-code-cli", "alt2")

        sel = select_credential(
            pool,
            "claude-code-cli",
            strategy=SelectionStrategy(SelectionPolicy.LEAST_ACTIVE, ("alt1", "alt2")),
            active_counter=active,
        )
        assert sel is not None
        assert sel.label == "alt2"

    def test_ties_broken_by_priority_order(self):

        pool = self._build_pool("alt1", "alt2")
        active = ActiveLeaseCounter()
        # Both at 0 active — priority order picks alt1.
        sel = select_credential(
            pool,
            "claude-code-cli",
            strategy=SelectionStrategy(SelectionPolicy.LEAST_ACTIVE, ("alt1", "alt2")),
            active_counter=active,
        )
        assert sel is not None
        assert sel.label == "alt1"

    def test_ramp_up_with_zero_counts_distributes(self):

        pool = self._build_pool("alt1", "alt2")
        active = ActiveLeaseCounter()
        counts: dict[str, int] = {"alt1": 0, "alt2": 0}
        for _ in range(20):
            sel = select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.LEAST_ACTIVE, ("alt1", "alt2")),
                active_counter=active,
            )
            assert sel is not None
            # Simulate the slot-coordinator's pattern: increment after
            # selection so the next call sees the +1.
            active.acquire("claude-code-cli", sel.label)
            counts[sel.label] += 1
        # With strictly-monotonic acquires (no releases yet), counts
        # alternate evenly.
        assert counts["alt1"] == 10
        assert counts["alt2"] == 10


class TestPriorityOrderBackwardsCompat:
    """Backwards-compat regression tests (plan-2026-05-03).

    Explicit ``--auth-policy priority-order`` must reproduce today's
    behavior bit-for-bit: alt1 returned for every concurrent slot
    until it cools.
    """

    def test_priority_order_returns_first_eligible_repeatedly(self):
        # Reproduces the P0-10 bug under explicit priority-order: every
        # one of N concurrent calls gets alt1 until alt1 cools.
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "alt1")
        _seed_active(pool, "claude-code-cli", "alt2")
        labels = []
        for _ in range(10):
            sel = select_credential(
                pool,
                "claude-code-cli",
                strategy=SelectionStrategy(SelectionPolicy.PRIORITY_ORDER, ("alt1", "alt2")),
            )
            assert sel is not None
            labels.append(sel.label)
        # Every call returns alt1 — the bug. ROUND_ROBIN fixes this.
        assert labels == ["alt1"] * 10


class TestActiveLeaseCounter:
    """In-process per-(adapter, label) active-lease counter."""

    def test_acquire_increments(self):

        c = ActiveLeaseCounter()
        c.acquire("claude-code-cli", "alt1")
        c.acquire("claude-code-cli", "alt1")
        assert c.get("claude-code-cli", "alt1") == 2

    def test_release_decrements(self):

        c = ActiveLeaseCounter()
        c.acquire("claude-code-cli", "alt1")
        c.acquire("claude-code-cli", "alt1")
        c.release("claude-code-cli", "alt1")
        assert c.get("claude-code-cli", "alt1") == 1

    def test_release_floors_at_zero(self):

        c = ActiveLeaseCounter()
        c.release("claude-code-cli", "alt1")
        assert c.get("claude-code-cli", "alt1") == 0

    def test_separate_keys_independent(self):

        c = ActiveLeaseCounter()
        c.acquire("claude-code-cli", "alt1")
        c.acquire("claude-code-cli", "alt2")
        c.acquire("codex-cli", "alt1")
        assert c.get("claude-code-cli", "alt1") == 1
        assert c.get("claude-code-cli", "alt2") == 1
        assert c.get("codex-cli", "alt1") == 1

    def test_snapshot_returns_defensive_copy(self):

        c = ActiveLeaseCounter()
        c.acquire("claude-code-cli", "alt1")
        snap = c.snapshot()
        # Mutating the snapshot must not affect the live counter.
        snap[("claude-code-cli", "alt1")] = 999
        assert c.get("claude-code-cli", "alt1") == 1


class TestAtomicCounter:
    """Atomic monotonic counter for ROUND_ROBIN selection."""

    def test_next_increments(self):

        c = AtomicCounter()
        assert c.next() == 0
        assert c.next() == 1
        assert c.next() == 2

    def test_peek_does_not_increment(self):

        c = AtomicCounter()
        c.next()
        assert c.peek() == 1
        assert c.peek() == 1


class TestSelectFallback:
    def test_policy_none_always_returns_none(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "laptop")
        _seed_active(pool, "claude-code-cli", "home")
        assert (
            select_fallback(
                pool,
                "claude-code-cli",
                exclude_labels=[("claude-code-cli", "laptop")],
                policy=FallbackPolicy.NONE,
                adapter_registry={},
            )
            is None
        )

    def test_same_provider_walks_sibling_labels(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "laptop")
        _seed_active(pool, "claude-code-cli", "home")
        sel = select_fallback(
            pool,
            "claude-code-cli",
            exclude_labels=[("claude-code-cli", "laptop")],
            policy=FallbackPolicy.SAME_PROVIDER,
            adapter_registry={},
        )
        assert sel is not None
        assert sel.label == "home"

    def test_same_provider_returns_none_when_all_excluded(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "laptop")
        sel = select_fallback(
            pool,
            "claude-code-cli",
            exclude_labels=[("claude-code-cli", "laptop")],
            policy=FallbackPolicy.SAME_PROVIDER,
            adapter_registry={},
        )
        assert sel is None

    def test_cross_provider_walks_declared_compat_list(self):
        # Source adapter declares Codex as compatible. Same-provider
        # exhausted → CROSS_PROVIDER should land on Codex.
        pool = _InMemoryPool()
        _seed_active(pool, "codex-cli", "work", fp="codex-fp")

        class _Src:
            compatible_fallback_adapters = ["codex-cli"]

        sel = select_fallback(
            pool,
            "claude-code-cli",
            exclude_labels=[],
            policy=FallbackPolicy.CROSS_PROVIDER,
            adapter_registry={"claude-code-cli": _Src()},
        )
        assert sel is not None
        assert sel.adapter == "codex-cli"
        assert sel.label == "work"

    def test_cross_provider_skips_same_adapter_labels(self):
        # CROSS_PROVIDER alone does NOT walk same-adapter siblings.
        # BOTH is the policy that does both in sequence.
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "home")  # would be same-provider hit
        _seed_active(pool, "codex-cli", "work")

        class _Src:
            compatible_fallback_adapters = ["codex-cli"]

        sel = select_fallback(
            pool,
            "claude-code-cli",
            exclude_labels=[("claude-code-cli", "laptop")],
            policy=FallbackPolicy.CROSS_PROVIDER,
            adapter_registry={"claude-code-cli": _Src()},
        )
        assert sel is not None
        assert sel.adapter == "codex-cli"

    def test_both_walks_same_first_then_cross(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "home")
        _seed_active(pool, "codex-cli", "work")

        class _Src:
            compatible_fallback_adapters = ["codex-cli"]

        sel = select_fallback(
            pool,
            "claude-code-cli",
            exclude_labels=[("claude-code-cli", "laptop")],
            policy=FallbackPolicy.BOTH,
            adapter_registry={"claude-code-cli": _Src()},
        )
        assert sel is not None
        assert sel.adapter == "claude-code-cli"
        assert sel.label == "home"

    def test_both_falls_through_to_cross_when_same_exhausted(self):
        pool = _InMemoryPool()
        _seed_active(pool, "codex-cli", "work")  # only codex available

        class _Src:
            compatible_fallback_adapters = ["codex-cli"]

        sel = select_fallback(
            pool,
            "claude-code-cli",
            exclude_labels=[],
            policy=FallbackPolicy.BOTH,
            adapter_registry={"claude-code-cli": _Src()},
        )
        assert sel is not None
        assert sel.adapter == "codex-cli"

    def test_cross_provider_returns_none_when_compat_empty(self):
        pool = _InMemoryPool()
        _seed_active(pool, "codex-cli", "work")

        class _Src:
            compatible_fallback_adapters: list[str] = []

        sel = select_fallback(
            pool,
            "claude-code-cli",
            exclude_labels=[],
            policy=FallbackPolicy.CROSS_PROVIDER,
            adapter_registry={"claude-code-cli": _Src()},
        )
        assert sel is None


class TestMarkHelpers:
    def test_mark_ok_flips_from_cooling(self):
        pool = _InMemoryPool()
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="cooling", fp="old", cooling_until_ts=99),
        )
        mark_ok(pool, "claude-code-cli", "laptop", new_blob="new-blob")
        e = pool.get_entry("claude-code-cli", "laptop")
        assert e.state.status == "active"
        assert e.blob == "new-blob"

    def test_mark_cooling_records_reset(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "laptop")
        mark_cooling(
            pool,
            "claude-code-cli",
            "laptop",
            cooling_until_ts=1234,
            reason="too_many_requests",
        )
        e = pool.get_entry("claude-code-cli", "laptop")
        assert e.state.status == "cooling"
        assert e.state.cooling_until_ts == 1234

    def test_mark_expired_flips_status(self):
        pool = _InMemoryPool()
        _seed_active(pool, "claude-code-cli", "laptop")
        mark_expired(pool, "claude-code-cli", "laptop", reason="invalid_grant")
        e = pool.get_entry("claude-code-cli", "laptop")
        assert e.state.status == "expired"
        assert e.state.expired_ts is not None


class TestWriteBackRotated:
    def test_no_rotation_does_not_push_new_blob(self):
        # When fingerprints match the credential blob is unchanged, so
        # no new secret version should be written. The state-only
        # last_ok_ts bump (verified separately) does flip the etag.
        pool = _InMemoryPool()

        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp=fingerprint_blob("same")),
            blob="same",
        )
        write_back_rotated(pool, "claude-code-cli", "laptop", new_blob="same")
        e = pool.get_entry("claude-code-cli", "laptop")
        # Blob unchanged: the helper did NOT push a fresh version.
        assert e.blob == "same"
        # FP unchanged for the same reason.
        assert e.state.fp == fingerprint_blob("same")

    def test_no_rotation_bumps_last_ok_ts(self):
        # Successful run with no rotation must still advance the
        # health timestamp so cooling/recovery selection logic sees
        # the label as healthy. Previously this was the bug jlevy
        # called out: write_back_rotated returned early on fp-match
        # and the label's last_ok_ts went stale.
        pool = _InMemoryPool()

        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(
                status="active",
                fp=fingerprint_blob("same"),
                last_ok_ts=1_700_000_000,  # stale
            ),
            blob="same",
        )
        write_back_rotated(pool, "claude-code-cli", "laptop", new_blob="same")
        e = pool.get_entry("claude-code-cli", "laptop")
        assert e.state.last_ok_ts is not None
        assert e.state.last_ok_ts > 1_700_000_000

    def test_no_rotation_recovers_cooling_label_past_reset(self):

        pool = _InMemoryPool()
        past_reset = int(_time.time()) - 600  # 10 min ago
        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(
                status="cooling",
                fp=fingerprint_blob("same"),
                cooling_until_ts=past_reset,
                last_quota_ts=past_reset - 600,
            ),
            blob="same",
        )
        write_back_rotated(pool, "claude-code-cli", "laptop", new_blob="same")
        e = pool.get_entry("claude-code-cli", "laptop")
        # The successful run must flip the label back to active so
        # the next selection treats it as eligible again.
        assert e.state.status == "active", "cooling label must recover on success"
        assert e.state.last_ok_ts is not None

    def test_writes_back_on_fingerprint_change(self):
        pool = _InMemoryPool()

        pool.seed(
            "claude-code-cli",
            "laptop",
            state=EntryState(status="active", fp=fingerprint_blob("old")),
            blob="old",
        )
        write_back_rotated(pool, "claude-code-cli", "laptop", new_blob="new")
        e = pool.get_entry("claude-code-cli", "laptop")
        assert e.blob == "new"
        assert e.state.fp == fingerprint_blob("new")
        assert e.state.status == "active"
