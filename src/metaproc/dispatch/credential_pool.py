"""Credential pool: storage + per-credential health + label selection.

The pool's job is to hand a healthy credential to each in-flight item.
Concurrency is owned by :class:`metaproc.runpool.pool.RunPool`;
``--max-concurrency`` controls how many items run simultaneously, and
RunPool's adaptive controller cuts that based on memory + API
back-pressure. The pool itself is *not* a mutex — many concurrent items
can use the same label simultaneously.

Surfaces:

- :class:`PoolEntry`, :class:`EntryState` — per-label shape; status is
  one of ``active`` / ``cooling`` / ``expired`` / ``disabled``.
- :class:`PoolBackend` protocol — :class:`GcpSecretManagerBackend` and
  :class:`LocalFilesystemBackend` implement it.
- :class:`SelectionPolicy` + :class:`SelectionStrategy` — how to pick
  a label among the eligible ones. Initial policy: priority-order
  failover. Future: round-robin, by-availability, etc.
- :func:`select_credential` — pure function (backend, adapter,
  strategy) → ``PoolSelection | None``.
- :func:`select_fallback` — cross-adapter walk under
  :class:`FallbackPolicy`; orthogonal to within-adapter selection.
- ``mark_ok`` / ``mark_cooling`` / ``mark_expired`` — status transitions
  driven by the slot coordinator's failure-classification path.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import json as _json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

from strif import atomic_output_file

from metaproc.io.mkdir_lock import mkdir_lock

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

# GCP Secret Manager label value constraint: `[a-z0-9_-]{0,63}`, value
# must be <= 63 chars. Operator labels are the stricter
# `[a-z0-9-]{1,40}` so the fully composed secret name stays readable.
_LABEL_VALUE_RE = re.compile(r"^[a-z0-9_-]{0,63}$")
_OPERATOR_LABEL_RE = re.compile(r"^[a-z0-9-]{1,40}$")

# Label name holding the Secret Manager version id this pool entry
# currently points to. Reads access this version directly rather than
# `latest` so a CAS'd label update controls BOTH the state flip and
# the payload pointer flip atomically — a failed CAS leaves readers
# on the prior version instead of a newly-pushed blob paired with
# stale labels. This ordering is part of the credential-pool consistency contract.
_ACTIVE_VERSION_LABEL = "active_version"


def validate_operator_label(label: str) -> None:
    """Raise ``ValueError`` if *label* is not a safe operator tag.

    Operator-chosen labels become part of the secret name and the
    ``label`` GCP label value. The constrained alphabet keeps both
    surfaces predictable and roundtrippable. Reject early so we never
    build a secret name we can't address.
    """
    if not _OPERATOR_LABEL_RE.match(label):
        msg = (
            f"invalid pool label {label!r}: must match [a-z0-9-]{{1,40}} "
            "(operator tag only, used in secret name + GCP label value)"
        )
        raise ValueError(msg)


def secret_name_for(adapter: str, user: str, label: str) -> str:
    """Return the GCP secret short name for ``(adapter, user, label)``.

    Format: ``<adapter-short>-auth-<user>-<label>``, where
    ``<adapter-short>`` strips the ``-cli`` suffix for brevity and
    matches the convention in Appendix A (``claude-code-auth-levy-laptop``).
    """
    validate_operator_label(label)
    short = adapter.removesuffix("-cli") if adapter.endswith("-cli") else adapter
    return f"{short}-auth-{user}-{label}"


def fingerprint_blob(blob: str) -> str:
    """Return the 12-char SHA-256 prefix used as the pool ``fp`` label."""
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _extract_version_id(version_name: str) -> str:
    """Return the numeric version suffix from a Secret Manager version name.

    ``add_secret_version`` returns a ``Version`` whose ``.name`` is
    ``projects/<p>/secrets/<s>/versions/<n>``; the pool stores only
    the ``<n>`` suffix in its ``active_version`` label.
    """
    return version_name.rsplit("/", 1)[-1]


# ── Pool entry dataclasses ───────────────────────────────────────


EntryStatus = Literal["active", "cooling", "expired", "disabled"]

QuotaGroupKind = Literal["org", "account", "unknown"]


class Vehicle(StrEnum):
    """Credential delivery shape for a pool label.

    Added in schema_version 2 (Vehicle A pool redesign — see
    ``docs/project/specs/active/plan-2026-04-28-claude-code-auth-vehicle-a-pool-redesign.md``).

    - ``OAUTH_TOKEN`` — static bearer credential injected via the
      ``CLAUDE_CODE_OAUTH_TOKEN`` env var. The pool stores the long-lived
      token (~1 year per Anthropic) directly in the blob field. Slot
      bootstrap does NOT materialize ``.credentials.json`` — the slot
      stays empty of stored credentials and the CLI reads only the env
      var. No refresh writeback path runs. This is the recommended
      primary pool credential.
    - ``LOGIN_CREDENTIALS`` — refresh-rotating OAuth session snapshot
      stored as a ``.credentials.json`` blob. The slot coordinator
      materializes ``<slot>/.credentials.json`` and the CLI may rotate
      the access token in place, writing back to the slot file (or, on
      macOS, to the OS Keychain — see research §F12). Used as the
      bootstrap path and per-label fallback path. Multi-process fan-out
      requires Vehicle B safe-mode (durable per-label state, lock, CAS
      writeback) — naive per-attempt copies become stale snapshots.

    Pre-v2 pool entries (schema_version 1) default to
    ``LOGIN_CREDENTIALS`` on read; that's what every legacy entry
    actually was.
    """

    OAUTH_TOKEN = "claude_code_oauth_token"
    LOGIN_CREDENTIALS = "login_credentials"


@dataclass(frozen=True)
class QuotaGroup:
    """Account-level rate-limit grouping for a pool label.

    Added in schema_version 2. The classifier's 429-cooling walk prefers
    labels in a *different* quota group on rate-limit failover, since
    Anthropic rate-limits at account level (and per ``organizationUuid``
    in some configurations — see ``anthropic/claude-code#41886``). Two
    labels in the same ``org:<uuid>`` group will 429 in lockstep, so
    failing over from one to the other doesn't help.

    - ``kind="org"`` — value is the ``organizationUuid`` from a Vehicle B
      credential blob, when known. Lowercased; dashes preserved (UUIDs
      satisfy the ``[a-z0-9_-]{0,63}`` GCP-label-value constraint).
    - ``kind="account"`` — value is a stable per-account identity hash
      (e.g. first 16 hex of sha256 over the account email or token).
      Used when ``organizationUuid`` is unavailable.
    - ``kind="unknown"`` — value is ``""``. The label has not been
      probed for quota grouping yet; the failover walk treats it
      pessimistically (assume it may share quota with any other unknown
      label).
    """

    kind: QuotaGroupKind
    value: str

    @classmethod
    def unknown(cls) -> QuotaGroup:
        """Return the canonical unknown quota group (``kind=unknown, value=""``)."""
        return cls(kind="unknown", value="")


@dataclass(frozen=True)
class EntryState:
    """Per-label credential health.

    Tracked solely for selection eligibility (``active`` is selectable;
    ``cooling`` / ``expired`` / ``disabled`` are not). Concurrency is
    owned by :class:`metaproc.runpool.pool.RunPool`; many concurrent
    items can use the same label simultaneously.

    ``cooling_until_ts = None`` means "cooling indefinitely" — selection
    treats it as ineligible until a probe or operator action reactivates
    it, rather than as "cooling forever" which would thrash on classifiers
    that couldn't parse a reset time.

    Schema_version 2 (2026-04-28, Vehicle A pool redesign) added
    ``vehicle``, ``account_id``, ``organization_uuid``, and
    ``quota_group``. Pre-v2 entries read with defaults
    (``vehicle=LOGIN_CREDENTIALS``, ``quota_group=unknown()``,
    ``account_id=organization_uuid=None``) — every legacy entry was a
    Vehicle B login-credentials snapshot.
    """

    status: EntryStatus
    fp: str
    last_ok_ts: int | None = None
    last_quota_ts: int | None = None
    cooling_until_ts: int | None = None
    expired_ts: int | None = None
    # ── schema_version 2 additions ───────────────────────────
    vehicle: Vehicle = Vehicle.LOGIN_CREDENTIALS
    account_id: str | None = None
    organization_uuid: str | None = None
    quota_group: QuotaGroup = field(default_factory=QuotaGroup.unknown)


@dataclass(frozen=True)
class PoolEntry:
    """A single labeled credential plus its state.

    ``blob`` carries the verbatim OAuth payload and MUST NOT be
    logged or printed outside the materialization path.  ``etag`` is
    the backend's optimistic-concurrency token; callers pass it back
    to ``upsert_entry`` to CAS on a lease or state flip.
    """

    adapter: str
    label: str
    blob: str
    state: EntryState
    etag: str


# ── Backend protocol ─────────────────────────────────────────────


@runtime_checkable
class PoolBackend(Protocol):
    """Storage backend protocol shared by GCP Secret Manager + local FS."""

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        """Read the latest version + label state for ``(adapter, label)``.

        Raises ``KeyError`` if the entry does not exist.
        """
        ...

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        """List all entries, optionally filtered by *adapter*.

        Backends must not include entries whose secrets are missing
        payloads — list is the surface operators read to understand
        which credentials they have, not a raw secret-name dump.
        """
        ...

    def upsert_entry(
        self,
        adapter: str,
        label: str,
        *,
        blob: str | None,
        state: EntryState | None,
        expected_etag: str | None = None,
    ) -> str:
        """Insert or update an entry; return new etag.

        Either or both of *blob* / *state* may be ``None``:
        - ``blob`` alone → add a new secret version (no state flip).
        - ``state`` alone → CAS a label/state flip (no version bump).
        - both → push + flip atomically (two SDK ops, same expected_etag).

        Raises ``ConcurrentModificationError`` on etag mismatch so
        callers can retry with a fresh read.
        """
        ...

    def delete_entry(self, adapter: str, label: str) -> None:
        """Delete the secret + all versions; idempotent."""
        ...


class ConcurrentModificationError(RuntimeError):
    """Raised by :meth:`PoolBackend.upsert_entry` on etag mismatch."""


# ── Label-safe codec (shared by GCP backend) ─────────────────────


def _int_to_label(value: int | None) -> str:
    # GCP labels require strings. Use decimal epoch; empty for None.
    return "" if value is None else str(int(value))


def _label_to_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def encode_labels(
    state: EntryState,
    *,
    adapter: str,
    pool_user: str,
    operator_label: str,
) -> dict[str, str]:
    """Serialize *state* into a GCP-label-safe dict.

    Every value must satisfy ``_LABEL_VALUE_RE`` (``[a-z0-9_-]{0,63}``).

    Schema_version 2 emits ``vehicle``, ``account_id``,
    ``organization_uuid``, ``quota_group_kind``, and
    ``quota_group_value`` alongside the v1 labels. Empty strings are
    valid label values (regex allows zero-length match), so ``None``
    fields and unknown quota groups encode without a placeholder
    sentinel.
    """
    validate_operator_label(operator_label)
    labels: dict[str, str] = {
        "adapter": adapter,
        "pool_user": pool_user,
        "label": operator_label,
        "status": state.status,
        "fp": state.fp,
        "last_ok_ts": _int_to_label(state.last_ok_ts),
        "last_quota_ts": _int_to_label(state.last_quota_ts),
        "cooling_until_ts": _int_to_label(state.cooling_until_ts),
        "expired_ts": _int_to_label(state.expired_ts),
        # ── schema_version 2 additions ───────────────────────
        "vehicle": state.vehicle.value,
        "account_id": state.account_id or "",
        "organization_uuid": state.organization_uuid or "",
        "quota_group_kind": state.quota_group.kind,
        "quota_group_value": state.quota_group.value,
    }
    # Defensive: GCP rejects any label value not matching the regex.
    for k, v in labels.items():
        if not _LABEL_VALUE_RE.match(v):
            msg = f"computed GCP label {k!r}={v!r} violates [a-z0-9_-]{{0,63}}; refusing to write"
            raise ValueError(msg)
    return labels


def decode_labels(labels: dict[str, str]) -> EntryState:
    """Deserialize a GCP-label dict back into :class:`EntryState`.

    Schema_version 2 fields default safely on missing labels: a v1 entry
    with no ``vehicle`` label decodes as ``LOGIN_CREDENTIALS`` (the only
    vehicle that existed pre-v2), and a v1 entry with no quota-group
    labels decodes as ``QuotaGroup.unknown()``.
    """
    status_raw = labels.get("status", "")
    if status_raw not in ("active", "cooling", "expired", "disabled"):
        # Unknown status — treat as disabled so a corrupt pool entry
        # can't accidentally be selected.
        status: EntryStatus = "disabled"
    else:
        status = status_raw  # type: ignore[assignment]

    vehicle_raw = labels.get("vehicle", "")
    if vehicle_raw == Vehicle.OAUTH_TOKEN.value:
        vehicle = Vehicle.OAUTH_TOKEN
    else:
        # v1 entries (no vehicle label) and unknown values both default
        # to LOGIN_CREDENTIALS — every legacy entry was Vehicle B, and
        # an unrecognized value should not silently be treated as the
        # static-bearer Vehicle A path.
        vehicle = Vehicle.LOGIN_CREDENTIALS

    qg_kind_raw = labels.get("quota_group_kind", "")
    if qg_kind_raw in ("org", "account", "unknown"):
        qg_kind: QuotaGroupKind = qg_kind_raw  # type: ignore[assignment]
    else:
        qg_kind = "unknown"
    # An unknown-kind group's value is structurally meaningless; force
    # to "" so two unknown groups compare equal regardless of any stale
    # value labels left from an earlier classification.
    qg_value = labels.get("quota_group_value", "") if qg_kind != "unknown" else ""

    return EntryState(
        status=status,
        fp=labels.get("fp", ""),
        last_ok_ts=_label_to_int(labels.get("last_ok_ts", "")),
        last_quota_ts=_label_to_int(labels.get("last_quota_ts", "")),
        cooling_until_ts=_label_to_int(labels.get("cooling_until_ts", "")),
        expired_ts=_label_to_int(labels.get("expired_ts", "")),
        vehicle=vehicle,
        account_id=labels.get("account_id") or None,
        organization_uuid=labels.get("organization_uuid") or None,
        quota_group=QuotaGroup(kind=qg_kind, value=qg_value),
    )


# ── GCP Secret Manager backend ───────────────────────────────────


@dataclass
class _GcpSdkAdapter:
    """Injection seam for tests.

    Holds the SDK client + project id.  Unit tests instantiate a fake
    with the same method surface so the backend doesn't pay network
    cost.  Production callers construct via :func:`gcp_backend`, which
    imports ``google.cloud.secretmanager`` lazily (the dep is gated by
    the ``gcp-batch`` extra).
    """

    client: Any
    project_id: str

    def secret_path(self, name: str) -> str:
        return f"projects/{self.project_id}/secrets/{name}"

    def version_path(self, name: str, version: str = "latest") -> str:
        return f"{self.secret_path(name)}/versions/{version}"


class GcpSecretManagerBackend:
    """Secret-Manager-backed :class:`PoolBackend`.

    Authenticates via Application Default Credentials (metadata server
    on Batch VMs, operator ``gcloud`` ADC on laptops).  Does NOT shell
    out to ``gcloud``: the agent container at
    ``devops/containers/Dockerfile.agent`` intentionally ships without
    the CLI, and re-introducing it would add ~500 MB to the image.

    CAS uses Secret Manager etags; see
    https://cloud.google.com/secret-manager/docs/etags.
    """

    def __init__(self, sdk: _GcpSdkAdapter, *, pool_user: str) -> None:
        self._sdk = sdk
        self._pool_user = pool_user

    # ── Read ──────────────────────────────────────────────────

    @staticmethod
    def _selected_version(labels: dict[str, str]) -> str:
        """Return the version id to read for a secret with *labels*.

        Prefer the ``active_version`` label so a rotation that adds a
        new version but fails to CAS its label update never causes
        readers to see the new blob paired with stale state. Falls
        back to ``latest`` for entries that predate the pointer scheme
        (e.g. a secret that was created but whose post-create label
        update failed — the fallback still serves the only version
        that exists).
        """
        active = labels.get(_ACTIVE_VERSION_LABEL, "").strip()
        return active or "latest"

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        name = secret_name_for(adapter, self._pool_user, label)
        secret = self._sdk.client.get_secret(name=self._sdk.secret_path(name))
        labels = dict(secret.labels)
        version_id = self._selected_version(labels)
        version = self._sdk.client.access_secret_version(
            name=self._sdk.version_path(name, version_id)
        )
        blob = version.payload.data.decode("utf-8")
        state = decode_labels(labels)
        return PoolEntry(
            adapter=adapter,
            label=label,
            blob=blob,
            state=state,
            etag=secret.etag,
        )

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        parent = f"projects/{self._sdk.project_id}"
        filter_parts = [f"labels.pool_user={self._pool_user}"]
        if adapter is not None:
            filter_parts.append(f"labels.adapter={adapter}")
        request = {"parent": parent, "filter": " AND ".join(filter_parts)}
        entries: list[PoolEntry] = []
        for secret in self._sdk.client.list_secrets(request=request):
            labels = dict(secret.labels)
            entry_adapter = labels.get("adapter", "")
            entry_label = labels.get("label", "")
            if not entry_adapter or not entry_label:
                # Not one of ours; skip without touching the blob.
                continue
            version_id = self._selected_version(labels)
            # Access version lazily so a half-created secret (no version
            # yet) doesn't crash the whole list call.
            try:
                version = self._sdk.client.access_secret_version(
                    name=f"{secret.name}/versions/{version_id}"
                )
            except Exception as exc:  # noqa: BLE001 — SDK exceptions vary
                log.warning(
                    "credential_pool: skipping %s (no accessible version): %s",
                    secret.name,
                    exc,
                )
                continue
            blob = version.payload.data.decode("utf-8")
            entries.append(
                PoolEntry(
                    adapter=entry_adapter,
                    label=entry_label,
                    blob=blob,
                    state=decode_labels(labels),
                    etag=secret.etag,
                )
            )
        return entries

    # ── Write ─────────────────────────────────────────────────

    def upsert_entry(
        self,
        adapter: str,
        label: str,
        *,
        blob: str | None,
        state: EntryState | None,
        expected_etag: str | None = None,
    ) -> str:
        name = secret_name_for(adapter, self._pool_user, label)
        secret_path = self._sdk.secret_path(name)
        # Create-or-update the secret so we can attach labels + a version.
        # CAS contract: when the caller passes expected_etag they own the
        # read-modify-write window and we MUST send their etag to
        # update_secret (not a fresh read from just now, which would
        # silently win a race). When expected_etag is None we read the
        # current etag and use it — callers doing unconditional updates
        # (fresh push / first-time create) take this path.
        try:
            existing = self._sdk.client.get_secret(name=secret_path)
        except Exception:  # noqa: BLE001 — NotFound semantics vary by SDK version
            existing = None
            if state is None:
                msg = f"pool entry {adapter}/{label} not found and no state provided to create it"
                raise KeyError(msg) from None
        existing_etag = (
            expected_etag
            if expected_etag is not None
            else (existing.etag if existing is not None else None)
        )

        # Ordering rule from the credential-pool consistency contract:
        #
        # For EXISTING entries we:
        # 1. add_secret_version (no CAS — bumps `latest`, returns new id)
        # 2. update_secret with (new state labels) AND
        #    (active_version = new id), CAS'd on expected_etag.
        #
        # Reads go through the active_version label (see
        # :meth:`_selected_version`), so if step 2 fails the label
        # still points at the previous version id and readers see
        # the prior blob + prior state. The new blob sits on the
        # secret as a dormant version until a future successful CAS
        # advances the pointer or `auth prune` cleans it up.
        #
        # For NEW entries we create_secret with labels (without a
        # pointer — there's no version to point at yet), add version 1,
        # then CAS labels to stamp active_version=1. If the final CAS
        # fails, reads fall back to `latest` via
        # :meth:`_selected_version` and still serve version 1 — the
        # only version — so the entry is usable.

        def _compose_labels(
            current_labels: dict[str, str],
            *,
            state_override: EntryState | None,
            active_version: str | None,
        ) -> dict[str, str]:
            """Merge a new state (or preserve existing state labels) with
            the active_version pointer. ``update_secret`` with
            ``update_mask=["labels"]`` replaces the full label map, so
            we must emit every label the pool cares about — dropping
            one would silently zero it out."""
            if state_override is not None:
                merged = encode_labels(
                    state_override,
                    adapter=adapter,
                    pool_user=self._pool_user,
                    operator_label=label,
                )
            else:
                # Preserve current state labels; we're only advancing
                # the pointer (e.g. a blob-only rotation or write-back).
                merged = {k: v for k, v in current_labels.items() if k != _ACTIVE_VERSION_LABEL}
            if active_version is not None:
                merged[_ACTIVE_VERSION_LABEL] = active_version
            elif _ACTIVE_VERSION_LABEL in current_labels:
                # Carry the existing pointer forward on label-only updates.
                merged[_ACTIVE_VERSION_LABEL] = current_labels[_ACTIVE_VERSION_LABEL]
            return merged

        if existing is None:
            if state is None:
                # Unreachable — the earlier branch raises. Kept for type clarity.
                msg = "internal: missing state for new entry"
                raise RuntimeError(msg)
            initial_labels = encode_labels(
                state,
                adapter=adapter,
                pool_user=self._pool_user,
                operator_label=label,
            )
            created = self._sdk.client.create_secret(
                parent=f"projects/{self._sdk.project_id}",
                secret_id=name,
                secret={
                    "replication": {"automatic": {}},
                    "labels": initial_labels,
                },
            )
            existing_etag = created.etag
            if blob is None:
                # Labeled shell with no version yet — operator will push
                # a blob later. Callers doing this pattern are rare but
                # shouldn't crash.
                return existing_etag
            version = self._sdk.client.add_secret_version(
                parent=secret_path,
                payload={"data": blob.encode("utf-8")},
            )
            new_version_id = _extract_version_id(version.name)
            # Stamp active_version in a follow-up CAS. A failure here
            # leaves the secret with labels but no active_version
            # pointer; `_selected_version` falls back to `latest` which
            # correctly serves version 1 (the only one).
            updated = self._sdk.client.update_secret(
                secret={
                    "name": secret_path,
                    "labels": _compose_labels(
                        dict(created.labels),
                        state_override=state,
                        active_version=new_version_id,
                    ),
                    "etag": existing_etag,
                },
                update_mask={"paths": ["labels"]},
            )
            log.info("credential_pool: pushed %s version %s", name, version.name)
            return updated.etag

        # Existing secret.
        current_labels = dict(existing.labels)
        new_version_id: str | None = None
        if blob is not None:
            version = self._sdk.client.add_secret_version(
                parent=secret_path,
                payload={"data": blob.encode("utf-8")},
            )
            new_version_id = _extract_version_id(version.name)
            log.info("credential_pool: pushed %s version %s", name, version.name)

        if state is not None or new_version_id is not None:
            # A label update is required for either a state flip or a
            # pointer advance (or both).
            updated = self._sdk.client.update_secret(
                secret={
                    "name": secret_path,
                    "labels": _compose_labels(
                        current_labels,
                        state_override=state,
                        active_version=new_version_id,
                    ),
                    "etag": existing_etag,
                },
                update_mask={"paths": ["labels"]},
            )
            existing_etag = updated.etag

        assert existing_etag is not None  # noqa: S101 — invariant: set above
        return existing_etag

    def delete_entry(self, adapter: str, label: str) -> None:
        name = secret_name_for(adapter, self._pool_user, label)
        try:
            self._sdk.client.delete_secret(name=self._sdk.secret_path(name))
        except Exception as exc:  # noqa: BLE001
            # NotFound semantics vary by SDK version; swallow so delete
            # stays idempotent for `auth prune --yes`.
            log.info("credential_pool: delete %s idempotent no-op: %s", name, exc)


# ── Local filesystem backend (Phase 2b) ──────────────────────────


class LocalFilesystemBackend:
    """Single-file PoolBackend backed by ``~/.metaproc/credentials.json``.

    Semantics match :class:`GcpSecretManagerBackend` exactly for the
    subset of operations the coordinator uses — same lease TTL, same
    ``active_version`` pointer (albeit trivial: local storage carries
    only one blob per label, so the pointer always names version 1),
    same cooling/expired state machine.

    Concurrency:
    - All writes go through :func:`strif.atomic_output_file` so readers
      never see a half-written file.
    - The read-modify-write window for a CAS is guarded by
      :func:`metaproc.io.mkdir_lock` (NFS-safe, unlike ``fcntl.flock``).
    - ``etag`` is ``"<mtime_ns>:<size>"`` of the underlying file — a
      cheap optimistic concurrency token that changes on every write.

    File schema (plan §Components 6, schema_version 2 from 2026-04-28):
        {
          "schema_version": 2,
          "adapters": {
            "claude-code-cli": {
              "entries": {
                "laptop": {
                  "blob": "...",
                  "state": {
                    "status": "active",
                    "fp": "abc123def456",
                    "last_ok_ts": 1700000000,
                    "last_quota_ts": null,
                    "cooling_until_ts": null,
                    "expired_ts": null,
                    "vehicle": "claude_code_oauth_token",
                    "account_id": "abc1234567890def",
                    "organization_uuid": null,
                    "quota_group": {"kind": "account", "value": "abc1234567890def"}
                  },
                  "created_ts": 1700000000,
                  "pushed_from": "keychain:Claude Code-credentials"
                }
              }
            }
          }
        }

    Single JSON file at ``~/.metaproc/credentials.json``; an
    ``mkdir_lock`` serializes concurrent ``upsert`` callers without
    requiring NFS-unsafe ``flock``.

    Schema migration: pre-v2 documents (``schema_version: 1``) are
    accepted on read with vehicle-and-quota-group defaults applied at
    record-decode time. The next ``upsert_entry`` call rewrites the
    document with ``schema_version: 2`` — the migration is in-place
    and transparent to operators. There is no reverse compat (v2 docs
    cannot be read by pre-v2 builds).
    """

    SCHEMA_VERSION = 2

    def __init__(self, *, path: Path, lock_timeout_s: float = 30.0) -> None:
        self._path = path
        self._lock_path = path.parent / ".credentials.lock"
        self._lock_timeout_s = lock_timeout_s

    # ── Read ──────────────────────────────────────────────────

    def _compute_etag(self) -> str:
        """Return the current on-disk etag or "" when the file is absent."""
        if not self._path.exists():
            return ""
        st = self._path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"

    def _load_document(self) -> dict[str, Any]:
        """Read + validate the JSON document, returning a defaulted shape on miss."""

        if not self._path.exists():
            return {"schema_version": self.SCHEMA_VERSION, "adapters": {}}
        try:
            raw = self._path.read_text()
        except OSError as exc:
            msg = f"failed to read {self._path}: {exc}"
            raise RuntimeError(msg) from exc
        if not raw.strip():
            return {"schema_version": self.SCHEMA_VERSION, "adapters": {}}
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            msg = f"{self._path}: expected JSON object at top level"
            raise RuntimeError(msg)
        doc_dict = cast("dict[str, Any]", doc)
        sv = doc_dict.get("schema_version", 0)
        if sv == 1:
            # v1 → v2 read shim (Vehicle A pool redesign, 2026-04-28).
            # v1 documents have no vehicle / account_id / organization_uuid
            # / quota_group fields; ``_entry_from_record`` applies safe
            # defaults at read time. The next write rewrites the doc with
            # schema_version 2 in place — migration is transparent.
            return doc_dict
        if sv != self.SCHEMA_VERSION:
            msg = (
                f"{self._path}: unsupported schema_version {sv!r}; "
                f"this build expects {self.SCHEMA_VERSION}. Back the "
                "file up and re-create with `metaproc auth push`."
            )
            raise RuntimeError(msg)
        return doc_dict

    def _entry_from_record(self, adapter: str, label: str, record: dict[str, Any]) -> PoolEntry:
        state_dict: Any = record.get("state") or {}
        if not isinstance(state_dict, dict):
            state_dict = {}
        state_typed = cast("dict[str, Any]", state_dict)
        status_raw = state_typed.get("status", "disabled")
        if status_raw not in ("active", "cooling", "expired", "disabled"):
            status: EntryStatus = "disabled"
        else:
            status = status_raw  # type: ignore[assignment]

        # ── schema_version 2 fields with v1 fallbacks ────────
        vehicle_raw = state_typed.get("vehicle", "")
        if vehicle_raw == Vehicle.OAUTH_TOKEN.value:
            vehicle = Vehicle.OAUTH_TOKEN
        else:
            # v1 records (no vehicle field) and unknown values both
            # default to LOGIN_CREDENTIALS — every legacy entry was
            # Vehicle B, and an unrecognized value should not silently
            # be treated as the static-bearer Vehicle A path.
            vehicle = Vehicle.LOGIN_CREDENTIALS

        qg_raw: Any = state_typed.get("quota_group")
        if isinstance(qg_raw, dict):
            qg_typed = cast("dict[str, Any]", qg_raw)
            qg_kind_raw = qg_typed.get("kind", "unknown")
            qg_kind: QuotaGroupKind = (
                qg_kind_raw if qg_kind_raw in ("org", "account", "unknown") else "unknown"
            )  # type: ignore[assignment]
            qg_value = qg_typed.get("value", "") if qg_kind != "unknown" else ""
            if not isinstance(qg_value, str):
                qg_value = ""
        else:
            qg_kind = "unknown"
            qg_value = ""

        account_id_raw = state_typed.get("account_id")
        organization_uuid_raw = state_typed.get("organization_uuid")
        state = EntryState(
            status=status,
            fp=state_typed.get("fp", ""),
            last_ok_ts=state_typed.get("last_ok_ts"),
            last_quota_ts=state_typed.get("last_quota_ts"),
            cooling_until_ts=state_typed.get("cooling_until_ts"),
            expired_ts=state_typed.get("expired_ts"),
            vehicle=vehicle,
            account_id=account_id_raw
            if isinstance(account_id_raw, str) and account_id_raw
            else None,
            organization_uuid=(
                organization_uuid_raw
                if isinstance(organization_uuid_raw, str) and organization_uuid_raw
                else None
            ),
            quota_group=QuotaGroup(kind=qg_kind, value=qg_value),
        )
        return PoolEntry(
            adapter=adapter,
            label=label,
            blob=record.get("blob", ""),
            state=state,
            etag=self._compute_etag(),
        )

    def get_entry(self, adapter: str, label: str) -> PoolEntry:
        doc = self._load_document()
        adapters_raw = doc.get("adapters")
        if not isinstance(adapters_raw, dict):
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        adapters = cast("dict[str, Any]", adapters_raw)
        block = adapters.get(adapter)
        entries = cast("dict[str, Any]", block).get("entries") if isinstance(block, dict) else None
        if not isinstance(entries, dict) or label not in cast("dict[str, Any]", entries):
            msg = f"no pool entry for {adapter}/{label}"
            raise KeyError(msg)
        return self._entry_from_record(adapter, label, cast("dict[str, Any]", entries)[label])

    def list_entries(self, adapter: str | None = None) -> list[PoolEntry]:
        doc = self._load_document()
        entries: list[PoolEntry] = []
        adapters_raw = doc.get("adapters", {})
        adapters = cast("dict[str, Any]", adapters_raw) if isinstance(adapters_raw, dict) else {}
        for a_name, block in adapters.items():
            if adapter is not None and a_name != adapter:
                continue
            if not isinstance(block, dict):
                continue
            entries_dict = cast("dict[str, Any]", block).get("entries", {})
            if not isinstance(entries_dict, dict):
                continue
            for label, record in cast("dict[str, Any]", entries_dict).items():
                if not isinstance(record, dict):
                    continue
                entries.append(
                    self._entry_from_record(a_name, label, cast("dict[str, Any]", record))
                )
        return entries

    # ── Write ─────────────────────────────────────────────────

    def _write_document(self, doc: dict[str, Any]) -> None:
        """Atomic write via strif.atomic_output_file + 0600 perms.

        Every write stamps ``schema_version`` to the current value, so a
        v1 document loaded via the read shim is automatically upgraded
        to v2 on its next mutation.
        """

        doc["schema_version"] = self.SCHEMA_VERSION
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Ensure parent dir is 0700 even when it pre-existed.
        with contextlib.suppress(OSError):
            self._path.parent.chmod(0o700)
        with atomic_output_file(self._path) as tmp:
            Path(tmp).write_text(_json.dumps(doc, indent=2))
            Path(tmp).chmod(0o600)

    def upsert_entry(
        self,
        adapter: str,
        label: str,
        *,
        blob: str | None,
        state: EntryState | None,
        expected_etag: str | None = None,
    ) -> str:

        validate_operator_label(label)
        with mkdir_lock(self._lock_path, timeout=self._lock_timeout_s):
            current_etag = self._compute_etag()
            if expected_etag is not None and current_etag != expected_etag and self._path.exists():
                msg = (
                    f"local backend etag mismatch for {adapter}/{label}: "
                    f"expected {expected_etag!r}, found {current_etag!r}"
                )
                raise ConcurrentModificationError(msg)
            doc = self._load_document()
            adapters = doc.setdefault("adapters", {})
            if not isinstance(adapters, dict):
                msg = f"{self._path}: 'adapters' must be a JSON object"
                raise RuntimeError(msg)
            adapters_typed = cast("dict[str, Any]", adapters)
            block = adapters_typed.setdefault(adapter, {"entries": {}})
            if not isinstance(block, dict):
                msg = f"{self._path}: adapter block for {adapter} is not an object"
                raise RuntimeError(msg)
            entries_map = cast("dict[str, Any]", block).setdefault("entries", {})
            if not isinstance(entries_map, dict):
                msg = f"{self._path}: 'entries' must be a JSON object"
                raise RuntimeError(msg)
            entries_typed = cast("dict[str, Any]", entries_map)
            existing_record = entries_typed.get(label)
            if not isinstance(existing_record, dict) and state is None:
                msg = (
                    f"local pool entry {adapter}/{label} not found and "
                    "no state provided to create it"
                )
                raise KeyError(msg)
            existing = (
                cast("dict[str, Any]", existing_record)
                if isinstance(existing_record, dict)
                else None
            )

            new_record: dict[str, Any] = dict(existing) if existing else {}
            if blob is not None:
                new_record["blob"] = blob
                if "created_ts" not in new_record:
                    new_record["created_ts"] = int(time.time())
            if state is not None:
                new_record["state"] = {
                    "status": state.status,
                    "fp": state.fp,
                    "last_ok_ts": state.last_ok_ts,
                    "last_quota_ts": state.last_quota_ts,
                    "cooling_until_ts": state.cooling_until_ts,
                    "expired_ts": state.expired_ts,
                    # ── schema_version 2 additions ───────────
                    "vehicle": state.vehicle.value,
                    "account_id": state.account_id,
                    "organization_uuid": state.organization_uuid,
                    "quota_group": {
                        "kind": state.quota_group.kind,
                        "value": state.quota_group.value,
                    },
                }
            elif "state" not in new_record:
                # New entry without state — guard above should preclude
                # this; sane default just in case.
                new_record["state"] = {
                    "status": "active",
                    "fp": "",
                    "last_ok_ts": None,
                    "last_quota_ts": None,
                    "cooling_until_ts": None,
                    "expired_ts": None,
                    "vehicle": Vehicle.LOGIN_CREDENTIALS.value,
                    "account_id": None,
                    "organization_uuid": None,
                    "quota_group": {"kind": "unknown", "value": ""},
                }
            entries_typed[label] = new_record
            self._write_document(doc)
        return self._compute_etag()

    def delete_entry(self, adapter: str, label: str) -> None:

        with mkdir_lock(self._lock_path, timeout=self._lock_timeout_s):
            doc = self._load_document()
            adapters = doc.get("adapters")
            if not isinstance(adapters, dict):
                return
            block = cast("dict[str, Any]", adapters).get(adapter)
            if not isinstance(block, dict):
                return
            entries_map = cast("dict[str, Any]", block).get("entries")
            if not isinstance(entries_map, dict):
                return
            entries_typed = cast("dict[str, Any]", entries_map)
            if label in entries_typed:
                del entries_typed[label]
                self._write_document(doc)


def local_backend(
    path: Path | None = None, *, lock_timeout_s: float = 30.0
) -> LocalFilesystemBackend:
    """Construct a LocalFilesystemBackend under ``~/.metaproc/credentials.json``.

    Operators can override the file path via ``path`` for scratch /
    test / multi-profile usage. The default is intentionally
    single-file so ``metaproc auth list`` is a cheap read.
    """
    actual = path or (Path.home() / ".metaproc" / "credentials.json")
    return LocalFilesystemBackend(path=actual, lock_timeout_s=lock_timeout_s)


# ── Convenience constructors ─────────────────────────────────────


def gcp_backend(project_id: str, *, pool_user: str) -> GcpSecretManagerBackend:
    """Construct a Secret-Manager-backed pool backend.

    Lazily imports ``google.cloud.secretmanager`` so laptop-only flows
    using the local backend don't pay the ``gcp-batch`` wheel cost.
    """
    try:
        from google.cloud import (  # noqa: PLC0415 -- optional [gcp-batch] dependency; type: ignore[import-not-found]
            secretmanager,
        )
    except ImportError as exc:  # pragma: no cover — handled at install time
        msg = (
            "google-cloud-secret-manager is required for the GCP pool "
            "backend. Install metaproc with the gcp-batch extra: "
            "pip install metaproc[gcp-batch]"
        )
        raise RuntimeError(msg) from exc

    client = secretmanager.SecretManagerServiceClient()
    sdk = _GcpSdkAdapter(client=client, project_id=project_id)
    return GcpSecretManagerBackend(sdk, pool_user=pool_user)


# ── State helpers (backend-agnostic) ─────────────────────────────


def state_mark_ok(state: EntryState, *, new_fp: str | None = None) -> EntryState:
    """Return a copy of *state* with ``last_ok_ts=now`` and (usually) ``status=active``.

    Status transitions:
    - ``cooling`` / ``expired`` / ``active`` → ``active`` (recovery / refresh).
    - ``disabled`` → ``disabled`` (preserved). Disabled is a manual operator
      pin-out; an in-flight item that completes successfully on a
      since-disabled label updates ``last_ok_ts`` for audit but must not
      auto-rehab the entry, otherwise the operator's intent (e.g. drain
      a label before reauth) is silently undone.

    If *new_fp* differs from the current fp, the fp is rotated too — a
    fresh push or a write-back-after-refresh both flow through here.
    """
    now = int(time.time())
    updates: dict[str, Any] = {"last_ok_ts": now}
    if state.status != "disabled":
        updates["status"] = "active"
    if new_fp is not None:
        updates["fp"] = new_fp
    return replace(state, **updates)


def state_mark_cooling(
    state: EntryState,
    *,
    cooling_until_ts: int | None,
) -> EntryState:
    """Return a copy of *state* in ``cooling`` status.

    ``cooling_until_ts = None`` means "cooling indefinitely until
    probe/operator action". Selection skips cooling entries until
    the timestamp passes; concurrent items already in flight on this
    label are unaffected (they finish their work — RunPool owns
    item lifetime).
    """
    now = int(time.time())
    return replace(
        state,
        status="cooling",
        last_quota_ts=now,
        cooling_until_ts=cooling_until_ts,
    )


def state_mark_expired(state: EntryState) -> EntryState:
    """Return a copy of *state* in ``expired`` status.

    Expiry is a marker, not destruction — an operator can re-enable
    via ``metaproc auth enable`` (which flips back to active only if a
    fresh push accompanies the flip). The blob is kept so a rotation
    can happen by version-add rather than secret-recreate.
    """
    now = int(time.time())
    return replace(state, status="expired", expired_ts=now)


_DEFAULT_SAFE_APPLY_MAX_ATTEMPTS = 3


def safe_apply_state(
    pool_backend: PoolBackend,
    *,
    adapter: str,
    label: str,
    entry: PoolEntry,
    compute_new: Callable[[EntryState], EntryState],
    max_attempts: int = _DEFAULT_SAFE_APPLY_MAX_ATTEMPTS,
) -> EntryState | None:
    """Apply a pool-state transition with race-tolerant CAS retry.

    Concurrent writers race on the backend's optimistic ``expected_etag``
    check (only one upsert wins, others raise ``ConcurrentModificationError``).
    On conflict we re-read the entry and recompute. Safe to repeat as long
    as ``compute_new`` is deterministic over the freshly-read state — true
    for the monotonic ``state_mark_ok`` / ``state_mark_expired`` /
    ``state_mark_cooling`` transitions.

    Returns the state actually written, or ``None`` if ``compute_new``
    returned the unchanged state on the latest read (no-op) or if all
    retry attempts lost the race.
    """
    current_entry = entry
    for _ in range(max_attempts):
        new_state = compute_new(current_entry.state)
        if new_state == current_entry.state:
            return None
        try:
            pool_backend.upsert_entry(
                adapter,
                label,
                blob=None,
                state=new_state,
                expected_etag=current_entry.etag,
            )
        except ConcurrentModificationError:
            current_entry = pool_backend.get_entry(adapter, label)
            continue
        return new_state
    # Exhausted retries — treat as a no-op observability write rather than
    # crashing the caller. The next caller will reflect the racing winner.
    return None


def eligible_labels(entries: Iterable[PoolEntry], *, now: int | None = None) -> list[PoolEntry]:
    """Filter *entries* to those eligible for selection.

    Active entries are eligible. Cooling entries are eligible iff
    ``cooling_until_ts <= now``. ``cooling_until_ts is None`` is
    NEVER eligible (cooling indefinitely — would thrash selection).
    """
    current = now if now is not None else int(time.time())
    out: list[PoolEntry] = []
    for entry in entries:
        if entry.state.status == "active" or (
            entry.state.status == "cooling"
            and entry.state.cooling_until_ts is not None
            and entry.state.cooling_until_ts <= current
        ):
            out.append(entry)
    return out


# ── Fallback policy + selection (plan §Design Approach, P2.1) ──


class RetryLaterPolicy(StrEnum):
    """What to do when ``select_fallback`` exhausts all eligible labels.

    Orthogonal to :class:`FallbackPolicy`: the fallback policy
    controls *spatial* recovery (walk labels across providers);
    this policy controls *temporal* recovery (wait / defer / bail).
    Both can be active simultaneously — fallback is tried first,
    and only if it returns no eligible label does this policy
    engage.

    - ``FAIL_FAST`` (default for non-deadline runs): caller raises
      :class:`PoolSlotUnavailableError` up to the dispatch layer.
    - ``WAIT``: caller blocks via
      :meth:`SlotCoordinator.wait_for_pool_recovery` until the
      earliest ``cooling_until_ts`` plus jitter, re-probing on
      wake. Bounded by ``METAPROC_AUTH_RETRY_MAX_WAIT`` (default 6h).
    - ``SIGNAL``: caller writes a ``retry_later.yaml`` checkpoint,
      emits a retry_later event, exits with code 78; an external
      resume daemon re-dispatches at ``cooling_until_ts``.
    """

    FAIL_FAST = "fail-fast"
    WAIT = "wait"
    SIGNAL = "signal"


class FallbackPolicy(StrEnum):
    """Scope of the slot coordinator's fallback walk.

    - ``NONE`` — fallback disabled. On cooling, the step fails with
      the classified error. Default for non-deadline runs.
    - ``SAME_PROVIDER`` — walk other labels on the same adapter.
    - ``CROSS_PROVIDER`` — walk only the adapters listed in the
      source adapter's ``compatible_fallback_adapters``, in order.
      Does NOT also walk same-adapter labels.
    - ``BOTH`` — same-provider first, then cross-provider.

    The coordinator retries a failing step at most once per policy
    regardless of the walk (OQ6) — same-provider + cross-provider
    counts are not additive.
    """

    NONE = "none"
    SAME_PROVIDER = "same-provider"
    CROSS_PROVIDER = "cross-provider"
    BOTH = "both"


@dataclass(frozen=True)
class PoolSelection:
    """Which labeled credential the coordinator picked for a slot.

    ``adapter`` MAY differ from the adapter the caller asked for —
    under ``CROSS_PROVIDER`` or ``BOTH`` the coordinator may swap to
    a compatible adapter. The lease is keyed on ``(adapter, label)``
    of the selection, not of the original request. ``secret_ref`` is
    the backend-agnostic handle used by the slot coordinator to
    re-fetch the blob under the lease; for the GCP backend this is
    ``projects/<p>/secrets/<name>/versions/<active_version>``. The
    ``fingerprint`` is the ``fp`` label of the selected entry — used
    to decide whether ``flush_refreshed_credential`` should write back.

    ``vehicle`` (schema_version 2) selects between the OAuth-token
    static-bearer path (``Vehicle.OAUTH_TOKEN``) and the legacy
    ``.credentials.json`` snapshot path (``Vehicle.LOGIN_CREDENTIALS``).
    The slot coordinator passes it through to the adapter's
    materialize / scope / scrub methods so dispatch and probe agree on
    the credential shape.
    """

    adapter: str
    label: str
    secret_ref: str
    fingerprint: str
    blob: str
    vehicle: Vehicle = Vehicle.LOGIN_CREDENTIALS


class SelectionPolicy(StrEnum):
    """How the credential pool picks a label when multiple are configured.

    Concurrency itself is owned by RunPool; this policy only controls
    which credential is handed to each item.

    - ``PRIORITY_ORDER`` — try the operator-supplied label list in
      order; every concurrent item converges on the first eligible
      label. Failover walks down the list when one cools/expires;
      recovery walks back up when the lead label returns to ``active``.
      The historical default; preserved for backwards compatibility.
      Suffers from the P0-10 load-balance failure under high
      concurrency: alt1 is returned for every concurrent slot until it
      cools, exhausting its 5h budget while alt2 sits idle.

    - ``ROUND_ROBIN`` — rotate through eligible labels via an atomic
      per-(adapter, run) counter. Each acquisition takes the next
      counter value mod the eligible-label count. Decisions are made
      at acquisition time (BEFORE the subprocess starts) so the
      ramp-up period — when no completion events exist yet —
      distributes evenly. The new dispatch default when
      ``--auth-include-labels`` has ≥ 2 labels.

    - ``LEAST_ACTIVE`` — pick the eligible label with the fewest
      active leases (NGINX-style least-connections). Robust when
      leases have varying duration or retries pile up on one label.
      Selectable via ``--auth-policy least-active`` for advanced
      cases.

    Both new policies make decisions at acquisition time, not
    completion time — counts based on completed ``auth_outcome``
    events alone are observability-grade, not load-balancing-grade.
    See plan-2026-05-03-auth-observability-and-load-balancing.md
    § Selection policies.
    """

    PRIORITY_ORDER = "priority-order"
    ROUND_ROBIN = "round-robin"
    LEAST_ACTIVE = "least-active"


@dataclass(frozen=True)
class SelectionStrategy:
    """How the credential pool selects a label for a dispatch attempt.

    ``labels`` is interpreted by ``policy``: for
    :attr:`SelectionPolicy.PRIORITY_ORDER` it's the priority list; for
    ``ROUND_ROBIN`` and ``LEAST_ACTIVE`` it's the eligible set (and
    the priority order used as a tiebreaker for ``LEAST_ACTIVE``). An
    empty ``labels`` tuple means "consider every active label for this
    adapter" — useful as a default when the operator didn't constrain
    the pool.
    """

    policy: SelectionPolicy = SelectionPolicy.PRIORITY_ORDER
    labels: tuple[str, ...] = ()


class AtomicCounter:
    """Thread-safe monotonic counter for ``ROUND_ROBIN`` selection.

    A single shared instance per (adapter, run) feeds round-robin
    indices to every concurrent ``select_credential`` call. The
    GIL-protected ``+=`` would suffice for CPython, but the explicit
    lock documents intent and survives no-GIL builds + free-threaded
    Python.
    """

    def __init__(self) -> None:

        self._value = 0
        self._lock = Lock()

    def next(self) -> int:
        """Return the current value, then increment. Atomic."""
        with self._lock:
            v = self._value
            self._value += 1
            return v

    def peek(self) -> int:
        """Return the current value without incrementing. Diagnostics only."""
        with self._lock:
            return self._value


class ActiveLeaseCounter:
    """Per-(adapter, label) active-lease accounting, in-process.

    Acquisition increments via :meth:`acquire`; release decrements via
    :meth:`release`, invoked from ``slot_coordinator.teardown`` after
    the subprocess exits (success OR failure — same path as the
    current ``auth_outcome`` emission).

    Non-negative invariant: a release without a matching acquire is a
    bug at the caller, but we floor at 0 rather than raise so a single
    accounting glitch doesn't poison the whole dispatch's selection.

    Cloud-orchestrator pools (cross-host) need a Filestore-backed
    shared counter — that's out of scope for this Phase 1 (see
    plan-2026-05-03 § Non-Goals). Local-mode and single-host cloud
    workers use this in-process counter.
    """

    def __init__(self) -> None:

        self._counts: dict[tuple[str, str], int] = {}
        self._lock = Lock()

    def acquire(self, adapter: str, label: str) -> None:
        with self._lock:
            key = (adapter, label)
            self._counts[key] = self._counts.get(key, 0) + 1

    def release(self, adapter: str, label: str) -> None:
        with self._lock:
            key = (adapter, label)
            current = self._counts.get(key, 0)
            self._counts[key] = max(0, current - 1)

    def get(self, adapter: str, label: str) -> int:
        with self._lock:
            return self._counts.get((adapter, label), 0)

    def snapshot(self) -> dict[tuple[str, str], int]:
        """Return a defensive copy of the counts. Safe to publish into
        events without further locking."""
        with self._lock:
            return dict(self._counts)


def select_credential(
    backend: PoolBackend,
    adapter: str,
    *,
    strategy: SelectionStrategy = SelectionStrategy(),
    exclude_labels: Iterable[tuple[str, str]] = (),
    now: int | None = None,
    active_counter: ActiveLeaseCounter | None = None,
    rr_counter: AtomicCounter | None = None,
) -> PoolSelection | None:
    """Pick a credential for a dispatch attempt under *strategy*.

    Returns ``None`` when no eligible label exists; the caller's
    :class:`RetryLaterPolicy` decides whether to wait or fail.

    Raises ``KeyError`` when ``strategy.labels`` references a label
    that has no pool entry (operator-typo guard, preserved bit-for-bit
    across the 2026-05-03 policy refactor).

    Policy semantics:

    - ``PRIORITY_ORDER`` (default): unchanged from pre-2026-05-03
      behavior. Returns the first eligible label in priority order.
    - ``ROUND_ROBIN``: requires ``rr_counter``; rotates through the
      eligible set via the shared atomic counter mod ``len(eligible)``.
    - ``LEAST_ACTIVE``: requires ``active_counter``; picks the
      eligible label with the lowest current active count, tying on
      priority-order position for determinism.

    Concurrency note: ``PRIORITY_ORDER`` is fully stateless — last
    writer to ``last_ok_ts`` / ``last_quota_ts`` wins. ``ROUND_ROBIN``
    and ``LEAST_ACTIVE`` rely on per-call counter state that's
    thread-safe but not multi-process-safe; cloud-orchestrator pools
    need a Filestore-backed shared counter (follow-up).
    """
    excluded: set[tuple[str, str]] = set(exclude_labels)
    all_entries = [e for e in backend.list_entries(adapter=adapter) if e.adapter == adapter]
    by_label = {e.label: e for e in all_entries}

    # Validate every label in the strategy actually exists — surfacing
    # operator typos at startup rather than as silent "no eligible
    # label". Preserved bit-for-bit from credential_pool.py:1330-1337
    # (pre-2026-05-03) so existing typo-guard tests stay green.
    for label in strategy.labels:
        if label not in by_label:
            available = sorted(by_label) or ["(none)"]
            msg = (
                f"no pool entry for {adapter}/{label}; "
                f"available labels for {adapter}: {', '.join(available)}"
            )
            raise KeyError(msg)

    ordered_labels = strategy.labels or tuple(sorted(by_label))

    if strategy.policy == SelectionPolicy.PRIORITY_ORDER:
        for label in ordered_labels:
            entry = by_label[label]
            if (entry.adapter, entry.label) in excluded:
                continue
            if entry in eligible_labels([entry], now=now):
                return _entry_to_selection(entry)
        return None

    # ROUND_ROBIN and LEAST_ACTIVE share the eligible-set computation:
    # walk priority order, drop excluded + ineligible, then pick by
    # policy-specific rule. Sharing here means a typo in priority
    # order surfaces identically across all three policies.
    eligible: list[PoolEntry] = []
    for label in ordered_labels:
        entry = by_label[label]
        if (entry.adapter, entry.label) in excluded:
            continue
        if entry not in eligible_labels([entry], now=now):
            continue
        eligible.append(entry)
    if not eligible:
        return None

    if strategy.policy == SelectionPolicy.ROUND_ROBIN:
        if rr_counter is None:
            msg = "ROUND_ROBIN requires rr_counter; pass an AtomicCounter via select_credential"
            raise ValueError(msg)
        idx = rr_counter.next() % len(eligible)
        return _entry_to_selection(eligible[idx])

    if strategy.policy == SelectionPolicy.LEAST_ACTIVE:
        if active_counter is None:
            msg = (
                "LEAST_ACTIVE requires active_counter; "
                "pass an ActiveLeaseCounter via select_credential"
            )
            raise ValueError(msg)
        # Sort by (active count asc, position in priority order). Ties
        # break on priority order so behavior is deterministic when
        # all counts are equal (notably during ramp-up: every label
        # has count=0 and we want priority-order's first eligible).
        position = {label: i for i, label in enumerate(ordered_labels)}
        eligible.sort(
            key=lambda e: (
                active_counter.get(e.adapter, e.label),
                position[e.label],
            )
        )
        return _entry_to_selection(eligible[0])

    msg = f"unsupported SelectionPolicy: {strategy.policy!r}"
    raise ValueError(msg)


def select_fallback(
    backend: PoolBackend,
    adapter: str,
    *,
    exclude_labels: Iterable[tuple[str, str]] = (),
    policy: FallbackPolicy,
    adapter_registry: Mapping[str, Any],
    now: int | None = None,
) -> PoolSelection | None:
    """Pick the next eligible credential under *policy*.

    ``exclude_labels`` is a collection of ``(adapter, label)`` pairs the
    coordinator has already tried on this step attempt — typically the
    labels whose leases just failed / went cooling. We skip them so a
    coordinator walking after ``select_primary`` returned a cooling
    label on attempt 1 doesn't loop back to the same label on attempt 2.

    ``adapter_registry`` is injected so tests can pass a minimal map
    without importing the real registry. It must carry
    ``AuthCapableCliAdapter`` instances; we read the
    ``compatible_fallback_adapters`` attribute for cross-provider walks.

    Returns ``None`` when the policy exhausts — caller consults
    ``RetryLaterPolicy`` (Phase 2c P2c.2/P2c.3).
    """
    if policy == FallbackPolicy.NONE:
        return None

    excluded: set[tuple[str, str]] = set(exclude_labels)

    def _pick_on(adapter_name: str) -> PoolSelection | None:
        entries = [
            e
            for e in backend.list_entries(adapter=adapter_name)
            if e.adapter == adapter_name and (e.adapter, e.label) not in excluded
        ]
        eligible = eligible_labels(entries, now=now)
        if not eligible:
            return None
        eligible.sort(key=lambda e: e.label)
        return _entry_to_selection(eligible[0])

    if policy == FallbackPolicy.SAME_PROVIDER:
        return _pick_on(adapter)

    # CROSS_PROVIDER or BOTH: consult the adapter's declared
    # compatible_fallback_adapters. BOTH walks same-provider first.
    if policy == FallbackPolicy.BOTH:
        same = _pick_on(adapter)
        if same is not None:
            return same

    src = adapter_registry.get(adapter)
    if src is None:
        return None
    compat = getattr(src, "compatible_fallback_adapters", None) or []
    for candidate_adapter in compat:
        pick = _pick_on(candidate_adapter)
        if pick is not None:
            return pick
    return None


def _entry_to_selection(entry: PoolEntry) -> PoolSelection:
    # The backend-specific secret_ref format is opaque to the
    # coordinator; GCP uses "projects/.../secrets/.../versions/<n>"
    # while LocalFilesystemBackend uses "local://<adapter>/<label>".
    # For Phase 1/2 we carry a synthesized ref the coordinator doesn't
    # interpret — the blob is the source of truth for materialization.
    secret_ref = f"pool://{entry.adapter}/{entry.label}"
    return PoolSelection(
        adapter=entry.adapter,
        label=entry.label,
        secret_ref=secret_ref,
        fingerprint=entry.state.fp,
        blob=entry.blob,
        vehicle=entry.state.vehicle,
    )


def mark_ok(
    backend: PoolBackend,
    adapter: str,
    label: str,
    *,
    new_blob: str | None = None,
    new_fingerprint: str | None = None,
) -> None:
    """Flip ``(adapter, label)`` to ``active`` + ``last_ok_ts=now``.

    If ``new_blob`` is supplied the backend also pushes a new
    Secret-Manager version (a fingerprint-gated write-back after a
    refresh-token rotation). ``new_fingerprint`` overrides the
    computed fp; callers that already fingerprinted the blob pass it
    through rather than recomputing.
    """
    try:
        entry = backend.get_entry(adapter, label)
    except KeyError:
        # Fresh push path goes through upsert_entry directly; this
        # helper is only for existing entries.
        return
    fp = new_fingerprint
    if fp is None and new_blob is not None:
        fp = fingerprint_blob(new_blob)
    new_state = state_mark_ok(entry.state, new_fp=fp)
    backend.upsert_entry(
        adapter,
        label,
        blob=new_blob,
        state=new_state,
        expected_etag=entry.etag,
    )


def mark_cooling(
    backend: PoolBackend,
    adapter: str,
    label: str,
    *,
    cooling_until_ts: int | None,
    reason: str,  # noqa: ARG001 — reserved for future event emission
) -> None:
    """Flip ``(adapter, label)`` to ``cooling`` and record the reset ts.

    ``cooling_until_ts = None`` means "cooling indefinitely until
    probe or operator action" — selection skips such labels.
    """
    try:
        entry = backend.get_entry(adapter, label)
    except KeyError:
        return
    new_state = state_mark_cooling(entry.state, cooling_until_ts=cooling_until_ts)
    backend.upsert_entry(adapter, label, blob=None, state=new_state, expected_etag=entry.etag)


def mark_expired(
    backend: PoolBackend,
    adapter: str,
    label: str,
    *,
    reason: str,  # noqa: ARG001 — reserved for future event emission
) -> None:
    """Flip ``(adapter, label)`` to ``expired`` (invalid_grant / auth error).

    Expiry is a marker, not destruction — operator can re-enable
    via ``metaproc auth push`` (a fresh blob from Keychain / ~/.codex/).
    """
    try:
        entry = backend.get_entry(adapter, label)
    except KeyError:
        return
    new_state = state_mark_expired(entry.state)
    backend.upsert_entry(adapter, label, blob=None, state=new_state, expected_etag=entry.etag)


def write_back_rotated(
    backend: PoolBackend,
    adapter: str,
    label: str,
    *,
    new_blob: str,
) -> None:
    """Fingerprint-gated write-back after a successful slot run.

    Two outcomes share this seam because real adapters return the
    on-disk blob from :meth:`flush_refreshed_credential` whether or
    not the CLI rotated the refresh token:

    1. Fingerprint changed → the CLI rotated the refresh token.
       Push a new secret version + flip ``fp`` + bump
       ``last_ok_ts`` + advance the ``active_version`` pointer in one
       CAS via the backend's pointer-atomic upsert (see
       :meth:`GcpSecretManagerBackend.upsert_entry`).

    2. Fingerprint unchanged → no rotation, but the run still
       succeeded. Fall through to :func:`mark_ok` so ``last_ok_ts``
       advances and any previously-cooling label whose reset has
       passed flips back to ``active``. Without this, a successful
       run on a label that was selected after its ``cooling_until_ts``
       expired would leave the entry stuck reported as cooling and
       the health timestamp stale.
    """
    new_fp = fingerprint_blob(new_blob)
    try:
        entry = backend.get_entry(adapter, label)
    except KeyError:
        return
    if entry.state.fp == new_fp:
        # No rotation, but the slot ran successfully. Apply the
        # ok-transition state-only so cooling→active recovers and
        # last_ok_ts reflects this run.
        mark_ok(backend, adapter, label)
        return
    new_state = state_mark_ok(entry.state, new_fp=new_fp)
    backend.upsert_entry(
        adapter,
        label,
        blob=new_blob,
        state=new_state,
        expected_etag=entry.etag,
    )


# Exported surface for later phase wiring; keeps this file the single
# source of truth for pool vocabulary.
__all__ = [
    "ActiveLeaseCounter",
    "AtomicCounter",
    "ConcurrentModificationError",
    "EntryState",
    "EntryStatus",
    "FallbackPolicy",
    "GcpSecretManagerBackend",
    "LocalFilesystemBackend",
    "PoolBackend",
    "PoolEntry",
    "PoolSelection",
    "RetryLaterPolicy",
    "SelectionPolicy",
    "SelectionStrategy",
    "decode_labels",
    "eligible_labels",
    "encode_labels",
    "fingerprint_blob",
    "gcp_backend",
    "local_backend",
    "mark_cooling",
    "mark_expired",
    "mark_ok",
    "safe_apply_state",
    "secret_name_for",
    "select_credential",
    "select_fallback",
    "state_mark_cooling",
    "state_mark_expired",
    "state_mark_ok",
    "validate_operator_label",
    "write_back_rotated",
]
