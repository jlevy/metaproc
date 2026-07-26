"""Tests for schema_version 2 fields on EntryState (Vehicle A pool redesign).

Spec: docs/project/specs/active/plan-2026-04-28-claude-code-auth-vehicle-a-pool-redesign.md
Bead: internal-reference (Phase 1 of epic internal-reference).

Exercises:
- :class:`Vehicle` enum + :class:`QuotaGroup` dataclass.
- :class:`EntryState` extended with vehicle / account_id /
  organization_uuid / quota_group fields, with schema-v1-compatible
  defaults.
- :func:`encode_labels` / :func:`decode_labels` round-trip the new
  fields and tolerate missing labels (v1 → v2 GCP-side compat).
- :class:`LocalFilesystemBackend` round-trips the new fields, accepts
  ``schema_version: 1`` documents on read with defaults applied, and
  upgrades them to ``schema_version: 2`` on the next write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.dispatch.credential_pool import (
    EntryState,
    LocalFilesystemBackend,
    QuotaGroup,
    Vehicle,
    decode_labels,
    encode_labels,
)


@pytest.fixture
def backend(tmp_path: Path) -> LocalFilesystemBackend:
    return LocalFilesystemBackend(path=tmp_path / "credentials.json")


class TestVehicleEnum:
    def test_oauth_token_value_is_stable_string(self):
        # The string value is on-disk state; changing it would break
        # in-place migration of existing pool files. Lock the value.
        assert Vehicle.OAUTH_TOKEN.value == "claude_code_oauth_token"

    def test_login_credentials_value_is_stable_string(self):
        assert Vehicle.LOGIN_CREDENTIALS.value == "login_credentials"

    def test_only_two_vehicles_exist(self):
        # Adding a vehicle is intentional + spec-driven — guard against
        # accidental additions by pinning the membership count.
        assert {v for v in Vehicle} == {Vehicle.OAUTH_TOKEN, Vehicle.LOGIN_CREDENTIALS}


class TestQuotaGroup:
    def test_unknown_factory_returns_canonical_unknown(self):
        qg = QuotaGroup.unknown()
        assert qg.kind == "unknown"
        assert qg.value == ""

    def test_unknown_groups_are_equal(self):
        # Used as a dataclass default_factory; equality must hold across
        # construction so dict / set semantics work as expected.
        assert QuotaGroup.unknown() == QuotaGroup.unknown()

    def test_org_group_carries_uuid_value(self):
        qg = QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab")
        assert qg.kind == "org"
        assert qg.value == "abc12345-6789-4abc-9def-0123456789ab"

    def test_account_group_carries_hex_value(self):
        qg = QuotaGroup(kind="account", value="abc1234567890def")
        assert qg.kind == "account"
        assert qg.value == "abc1234567890def"


class TestEntryStateDefaults:
    def test_minimum_construction_uses_schema_v2_defaults(self):
        # Existing call sites pass only status + fp; new fields must
        # default to a Vehicle B / unknown-quota shape so legacy pool
        # entries do not change classification.
        s = EntryState(status="active", fp="abc")
        assert s.vehicle == Vehicle.LOGIN_CREDENTIALS
        assert s.account_id is None
        assert s.organization_uuid is None
        assert s.quota_group == QuotaGroup.unknown()

    def test_explicit_vehicle_a_construction(self):
        s = EntryState(
            status="active",
            fp="abc",
            vehicle=Vehicle.OAUTH_TOKEN,
            account_id="abc1234567890def",
            organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
            quota_group=QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab"),
        )
        assert s.vehicle == Vehicle.OAUTH_TOKEN
        assert s.account_id == "abc1234567890def"
        assert s.quota_group.kind == "org"


class TestGcpLabelCodec:
    def test_round_trip_vehicle_a_with_org_quota_group(self):
        original = EntryState(
            status="active",
            fp="fp1",
            last_ok_ts=1700000000,
            vehicle=Vehicle.OAUTH_TOKEN,
            account_id="abc1234567890def",
            organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
            quota_group=QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab"),
        )
        labels = encode_labels(
            original,
            adapter="claude-code-cli",
            pool_user="levy",
            operator_label="laptop",
        )
        decoded = decode_labels(labels)
        assert decoded == original

    def test_round_trip_vehicle_b_with_unknown_quota_group(self):
        original = EntryState(status="active", fp="fp1")
        labels = encode_labels(
            original,
            adapter="claude-code-cli",
            pool_user="levy",
            operator_label="laptop",
        )
        decoded = decode_labels(labels)
        assert decoded == original
        # Defaults should be implicit, not require explicit storage.
        assert decoded.vehicle == Vehicle.LOGIN_CREDENTIALS
        assert decoded.quota_group == QuotaGroup.unknown()

    def test_decode_v1_labels_without_new_fields_uses_defaults(self):
        # Simulate a GCP secret labelled by a pre-v2 builder: no
        # vehicle / account_id / organization_uuid / quota_group_*
        # labels at all.
        v1_labels = {
            "adapter": "claude-code-cli",
            "pool_user": "levy",
            "label": "laptop",
            "status": "active",
            "fp": "fp-from-v1",
            "last_ok_ts": "",
            "last_quota_ts": "",
            "cooling_until_ts": "",
            "expired_ts": "",
        }
        decoded = decode_labels(v1_labels)
        assert decoded.status == "active"
        assert decoded.fp == "fp-from-v1"
        # Defaults applied at decode time.
        assert decoded.vehicle == Vehicle.LOGIN_CREDENTIALS
        assert decoded.account_id is None
        assert decoded.organization_uuid is None
        assert decoded.quota_group == QuotaGroup.unknown()

    def test_decode_unknown_vehicle_value_falls_back_to_login_credentials(self):
        # An unrecognized vehicle string must NOT silently be treated as
        # the static-bearer Vehicle A path — that would let a corrupt
        # entry skip the .credentials.json materialization safely.
        labels = {
            "adapter": "claude-code-cli",
            "pool_user": "levy",
            "label": "laptop",
            "status": "active",
            "fp": "fp",
            "last_ok_ts": "",
            "last_quota_ts": "",
            "cooling_until_ts": "",
            "expired_ts": "",
            "vehicle": "future_vehicle_kind_we_do_not_know_about",
            "account_id": "",
            "organization_uuid": "",
            "quota_group_kind": "unknown",
            "quota_group_value": "",
        }
        decoded = decode_labels(labels)
        assert decoded.vehicle == Vehicle.LOGIN_CREDENTIALS

    def test_decode_unknown_quota_group_kind_falls_back_to_unknown(self):
        labels = {
            "adapter": "claude-code-cli",
            "pool_user": "levy",
            "label": "laptop",
            "status": "active",
            "fp": "fp",
            "last_ok_ts": "",
            "last_quota_ts": "",
            "cooling_until_ts": "",
            "expired_ts": "",
            "vehicle": Vehicle.OAUTH_TOKEN.value,
            "account_id": "",
            "organization_uuid": "",
            "quota_group_kind": "future_kind",
            "quota_group_value": "leftover-value-should-be-discarded",
        }
        decoded = decode_labels(labels)
        # Unknown kind forces canonical empty value.
        assert decoded.quota_group == QuotaGroup.unknown()

    def test_uuid_with_dashes_is_a_valid_label_value(self):
        # GCP labels accept dashes — full UUID form must encode without
        # the codec rejecting it.
        original = EntryState(
            status="active",
            fp="fp",
            organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
            quota_group=QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab"),
        )
        labels = encode_labels(
            original,
            adapter="claude-code-cli",
            pool_user="levy",
            operator_label="laptop",
        )
        assert labels["organization_uuid"] == "abc12345-6789-4abc-9def-0123456789ab"
        assert labels["quota_group_value"] == "abc12345-6789-4abc-9def-0123456789ab"


class TestLocalBackendRoundTrip:
    def test_vehicle_a_entry_round_trips(self, backend: LocalFilesystemBackend):
        original = EntryState(
            status="active",
            fp="fp-vehicle-a",
            vehicle=Vehicle.OAUTH_TOKEN,
            account_id="abc1234567890def",
            organization_uuid="abc12345-6789-4abc-9def-0123456789ab",
            quota_group=QuotaGroup(kind="org", value="abc12345-6789-4abc-9def-0123456789ab"),
        )
        backend.upsert_entry(
            "claude-code-cli",
            "alt1",
            blob="oauth-token-string-not-credentials-json",
            state=original,
        )
        readback = backend.get_entry("claude-code-cli", "alt1").state
        assert readback == original

    def test_vehicle_b_entry_round_trips_with_defaults(self, backend: LocalFilesystemBackend):
        original = EntryState(status="active", fp="fp-legacy-b")
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob='{"claudeAiOauth": {"accessToken": "..."}}',
            state=original,
        )
        readback = backend.get_entry("claude-code-cli", "laptop").state
        assert readback == original
        assert readback.vehicle == Vehicle.LOGIN_CREDENTIALS
        assert readback.quota_group == QuotaGroup.unknown()


class TestSchemaMigrationV1ToV2:
    def test_v1_document_reads_with_v2_defaults(self, tmp_path: Path):
        # Hand-craft a v1 JSON document — exactly the shape an older
        # build wrote — and verify the new backend reads it cleanly.
        path = tmp_path / "credentials.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        v1_doc = {
            "schema_version": 1,
            "adapters": {
                "claude-code-cli": {
                    "entries": {
                        "laptop": {
                            "blob": "legacy-blob",
                            "state": {
                                "status": "active",
                                "fp": "legacy-fp",
                                "last_ok_ts": 1700000000,
                                "last_quota_ts": None,
                                "cooling_until_ts": None,
                                "expired_ts": None,
                            },
                            "created_ts": 1700000000,
                        }
                    }
                }
            },
        }
        path.write_text(json.dumps(v1_doc))
        backend = LocalFilesystemBackend(path=path)
        entry = backend.get_entry("claude-code-cli", "laptop")
        assert entry.state.status == "active"
        assert entry.state.fp == "legacy-fp"
        assert entry.state.last_ok_ts == 1700000000
        # v1 → v2 defaults applied at read.
        assert entry.state.vehicle == Vehicle.LOGIN_CREDENTIALS
        assert entry.state.account_id is None
        assert entry.state.organization_uuid is None
        assert entry.state.quota_group == QuotaGroup.unknown()

    def test_first_write_after_v1_read_upgrades_doc_to_v2(self, tmp_path: Path):
        path = tmp_path / "credentials.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        v1_doc = {
            "schema_version": 1,
            "adapters": {
                "claude-code-cli": {
                    "entries": {
                        "laptop": {
                            "blob": "legacy",
                            "state": {"status": "active", "fp": "legacy-fp"},
                        }
                    }
                }
            },
        }
        path.write_text(json.dumps(v1_doc))
        backend = LocalFilesystemBackend(path=path)
        # Trigger a write — adding a second label.
        backend.upsert_entry(
            "claude-code-cli",
            "alt1",
            blob="new",
            state=EntryState(
                status="active",
                fp="new-fp",
                vehicle=Vehicle.OAUTH_TOKEN,
            ),
        )
        # Re-read the file directly — must now be v2.
        on_disk = json.loads(path.read_text())
        assert on_disk["schema_version"] == 2
        # Both entries readable; the legacy one keeps its v1 shape with
        # defaults applied at decode time, the new one is full v2.
        legacy_state = backend.get_entry("claude-code-cli", "laptop").state
        assert legacy_state.vehicle == Vehicle.LOGIN_CREDENTIALS
        new_state = backend.get_entry("claude-code-cli", "alt1").state
        assert new_state.vehicle == Vehicle.OAUTH_TOKEN

    def test_v2_document_reads_unchanged(self, tmp_path: Path):
        # Sanity: writing then reading on the same backend is idempotent.
        path = tmp_path / "credentials.json"
        backend = LocalFilesystemBackend(path=path)
        backend.upsert_entry(
            "claude-code-cli",
            "alt1",
            blob="b",
            state=EntryState(
                status="active",
                fp="fp",
                vehicle=Vehicle.OAUTH_TOKEN,
                quota_group=QuotaGroup(kind="account", value="abc1234567890def"),
            ),
        )
        on_disk = json.loads(path.read_text())
        assert on_disk["schema_version"] == 2

    def test_unsupported_future_schema_version_still_rejected(self, tmp_path: Path):
        # The v1 → v2 read shim must not loosen rejection of genuinely
        # unknown future versions — operators should see a clear error
        # rather than silent default application.
        path = tmp_path / "credentials.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 999, "adapters": {}}))
        backend = LocalFilesystemBackend(path=path)
        with pytest.raises(RuntimeError, match="unsupported schema_version"):
            backend.list_entries()


class TestVehicleChangeViaRePush:
    def test_rotation_from_login_credentials_to_oauth_token(self, backend: LocalFilesystemBackend):
        # Operator first pushed as Vehicle B (e.g. from Keychain), then
        # rotated to Vehicle A by minting a setup-token. The vehicle
        # field on the latest entry must reflect the new vehicle.
        backend.upsert_entry(
            "claude-code-cli",
            "alt1",
            blob='{"claudeAiOauth": {"accessToken": "..."}}',
            state=EntryState(
                status="active",
                fp="fp-b",
                vehicle=Vehicle.LOGIN_CREDENTIALS,
            ),
        )
        backend.upsert_entry(
            "claude-code-cli",
            "alt1",
            blob="oauth-token-string",
            state=EntryState(
                status="active",
                fp="fp-a",
                vehicle=Vehicle.OAUTH_TOKEN,
                account_id="abc1234567890def",
            ),
        )
        readback = backend.get_entry("claude-code-cli", "alt1")
        assert readback.state.vehicle == Vehicle.OAUTH_TOKEN
        assert readback.state.account_id == "abc1234567890def"
        assert readback.blob == "oauth-token-string"
