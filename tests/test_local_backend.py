"""Tests for metaproc.dispatch.credential_pool.LocalFilesystemBackend (P2b.2).

Exercises the PoolBackend protocol against a tmp file — same shape
as GcpSecretManagerBackend tests but with filesystem concurrency,
etag-via-mtime-size, and the full-holder persistence semantics.
"""

from __future__ import annotations

import json
import stat
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.adapters.claude_cli import ClaudeCodeCliAdapter
from metaproc.cli import app
from metaproc.dispatch.credential_pool import (
    ConcurrentModificationError,
    EntryState,
    LocalFilesystemBackend,
    PoolBackend,
    fingerprint_blob,
    local_backend,
)


@pytest.fixture
def backend(tmp_path):
    path = tmp_path / "credentials.json"
    return LocalFilesystemBackend(path=path)


class TestLocalBackendProtocolConformance:
    def test_is_pool_backend(self, backend):
        # PoolBackend is runtime_checkable — isinstance works.
        assert isinstance(backend, PoolBackend)

    def test_default_constructor_uses_home_metaproc(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        b = local_backend()
        assert b._path == tmp_path / ".metaproc" / "credentials.json"  # noqa: SLF001


class TestCreateReadRoundTrip:
    def test_push_and_get_roundtrips(self, backend):
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="my-blob",
            state=EntryState(status="active", fp=fingerprint_blob("my-blob")),
        )
        entry = backend.get_entry("claude-code-cli", "laptop")
        assert entry.blob == "my-blob"
        assert entry.state.status == "active"
        assert entry.state.fp == fingerprint_blob("my-blob")

    def test_file_and_parent_dir_have_correct_permissions(self, backend):
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="x",
            state=EntryState(status="active", fp="fp"),
        )
        assert backend._path.exists()  # noqa: SLF001
        mode = stat.S_IMODE(backend._path.stat().st_mode)  # noqa: SLF001
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"
        parent_mode = stat.S_IMODE(backend._path.parent.stat().st_mode)  # noqa: SLF001
        # Parent dir MUST be 0700 — we may have a pre-existing dir
        # the backend hardens on first write.
        assert parent_mode == 0o700

    def test_get_missing_entry_raises_keyerror(self, backend):
        with pytest.raises(KeyError):
            backend.get_entry("claude-code-cli", "ghost")

    def test_list_entries_filters_by_adapter(self, backend):
        for label in ("laptop", "home"):
            backend.upsert_entry(
                "claude-code-cli",
                label,
                blob=f"blob-{label}",
                state=EntryState(status="active", fp=f"fp-{label}"),
            )
        backend.upsert_entry(
            "codex-cli",
            "work",
            blob="blob-codex",
            state=EntryState(status="active", fp="fp-codex"),
        )
        assert len(backend.list_entries()) == 3
        claude_entries = backend.list_entries(adapter="claude-code-cli")
        assert {e.label for e in claude_entries} == {"laptop", "home"}
        assert len(backend.list_entries(adapter="codex-cli")) == 1

    def test_list_on_empty_file_returns_empty(self, backend):
        assert backend.list_entries() == []


class TestEtagCAS:
    def test_write_bumps_etag(self, backend):
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="x",
            state=EntryState(status="active", fp="fp"),
        )
        e1 = backend.get_entry("claude-code-cli", "laptop").etag
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob=None,
            state=EntryState(status="cooling", fp="fp", cooling_until_ts=1),
            expected_etag=e1,
        )
        e2 = backend.get_entry("claude-code-cli", "laptop").etag
        assert e1 != e2

    def test_stale_etag_raises_concurrent_modification(self, backend):
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="x",
            state=EntryState(status="active", fp="fp"),
        )
        stale = backend.get_entry("claude-code-cli", "laptop").etag
        # External mutation bumps the on-disk etag.
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob=None,
            state=EntryState(status="active", fp="fp-new"),
        )
        # Now attempt a write with the stale token.
        with pytest.raises(ConcurrentModificationError):
            backend.upsert_entry(
                "claude-code-cli",
                "laptop",
                blob=None,
                state=EntryState(status="disabled", fp="fp-new"),
                expected_etag=stale,
            )


class TestSchemaVersioning:
    def test_unsupported_schema_version_is_rejected(self, backend):
        # Write a file with a bogus schema version.
        backend._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # noqa: SLF001
        backend._path.write_text(  # noqa: SLF001
            json.dumps({"schema_version": 999, "adapters": {}})
        )
        with pytest.raises(RuntimeError, match="unsupported schema_version"):
            backend.list_entries()

    def test_empty_file_treated_as_empty_pool(self, backend):
        backend._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # noqa: SLF001
        backend._path.write_text("")  # noqa: SLF001
        assert backend.list_entries() == []


class TestDelete:
    def test_delete_is_idempotent(self, backend):
        # Deleting a non-existent entry is a no-op.
        backend.delete_entry("claude-code-cli", "ghost")
        # Existing → gone.
        backend.upsert_entry(
            "claude-code-cli",
            "laptop",
            blob="x",
            state=EntryState(status="active", fp="fp"),
        )
        backend.delete_entry("claude-code-cli", "laptop")
        with pytest.raises(KeyError):
            backend.get_entry("claude-code-cli", "laptop")


class TestMkdirLockContention:
    """P2b.7: two concurrent upserts must serialize, both succeed."""

    def test_two_concurrent_pushes_both_land(self, tmp_path):
        path = tmp_path / "credentials.json"
        b1 = LocalFilesystemBackend(path=path)
        b2 = LocalFilesystemBackend(path=path)

        errors: list[Exception] = []

        def push(backend: LocalFilesystemBackend, label: str) -> None:
            try:
                backend.upsert_entry(
                    "claude-code-cli",
                    label,
                    blob=f"blob-{label}",
                    state=EntryState(status="active", fp=f"fp-{label}"),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=push, args=(b1, "laptop"))
        t2 = threading.Thread(target=push, args=(b2, "home"))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert not errors, f"concurrent pushes raised: {errors}"

        # Both labels must be present — the second writer must not
        # have clobbered the first's blob.
        labels = {e.label for e in b1.list_entries()}
        assert labels == {"laptop", "home"}


class TestBackendFlagWiring:
    """P2b.3: --backend local is wired into the factory."""

    def test_auth_push_with_backend_local(self, tmp_path, monkeypatch):
        # Point HOME at a tmp dir so the local backend writes there.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        monkeypatch.setattr(
            ClaudeCodeCliAdapter,
            "capture_credential",
            lambda self: '{"claudeAiOauth":{"accessToken":"sk-test"}}',
        )
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "auth",
                "push",
                "--adapter",
                "claude-code-cli",
                "--label",
                "laptop",
                "--backend",
                "local",
                # Phase 10: claude-code-cli's default --vehicle is now
                # oauth-token. This test exercises the legacy V-B
                # Keychain-backed push path (mock_capture above), so
                # opt into login-credentials explicitly.
                "--vehicle",
                "login-credentials",
            ],
        )
        assert result.exit_code == 0, result.stderr
        # Credential file landed at ~/.metaproc/credentials.json.
        assert (tmp_path / ".metaproc" / "credentials.json").exists()
