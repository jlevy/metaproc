"""Tests for metaproc claude-auth command."""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands import claude_auth

runner = CliRunner()

VALID_PAYLOAD = json.dumps(
    {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-fake",
            "refreshToken": "sk-ant-ort01-fake",
            "expiresAt": 1_800_000_000_000,
            "scopes": ["user:inference"],
            "subscriptionType": "max",
        }
    }
)


# ── Helpers ──────────────────────────────────────────────────────


class _GcloudRecorder:
    """Record gcloud invocations and return staged stdout per arg-prefix."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.stdout_map: dict[tuple[str, ...], str] = {}
        self.fail_prefixes: set[tuple[str, ...]] = set()

    def set_stdout(self, prefix: tuple[str, ...], stdout: str) -> None:
        self.stdout_map[prefix] = stdout

    def set_fail(self, prefix: tuple[str, ...]) -> None:
        self.fail_prefixes.add(prefix)

    def __call__(
        self,
        args: list[str],
        *,
        stdin: str | None = None,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append({"args": list(args), "stdin": stdin, "capture": capture})
        for prefix in self.fail_prefixes:
            if tuple(args[: len(prefix)]) == prefix:
                raise subprocess.CalledProcessError(1, ["gcloud", *args], stderr="fail")
        stdout = ""
        for prefix, staged in self.stdout_map.items():
            if tuple(args[: len(prefix)]) == prefix:
                stdout = staged
                break
        return subprocess.CompletedProcess(
            args=["gcloud", *args], returncode=0, stdout=stdout, stderr=""
        )


@pytest.fixture
def gcloud(monkeypatch: pytest.MonkeyPatch) -> _GcloudRecorder:
    rec = _GcloudRecorder()
    monkeypatch.setattr(claude_auth, "_gcloud", rec)
    return rec


@pytest.fixture
def darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def keychain_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_auth, "_read_keychain_blob", lambda: VALID_PAYLOAD)


@pytest.fixture
def _project_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPROC_GCP_PROJECT", "test-project")
    monkeypatch.setenv("METAPROC_GCP_SERVICE_ACCOUNT", "user@example.invalid")


# ── _require_darwin ──────────────────────────────────────────────


class TestRequireDarwin:
    def test_passes_on_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        claude_auth._require_darwin()

    def test_raises_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(typer.BadParameter, match="requires macOS"):
            claude_auth._require_darwin()


# ── _validate_creds_payload ──────────────────────────────────────


class TestValidatePayload:
    def test_accepts_valid(self) -> None:
        claude_auth._validate_creds_payload(VALID_PAYLOAD)

    def test_rejects_non_json(self) -> None:
        with pytest.raises(RuntimeError, match="not valid JSON"):
            claude_auth._validate_creds_payload("{not json")

    def test_rejects_missing_key(self) -> None:
        with pytest.raises(RuntimeError, match="claudeAiOauth"):
            claude_auth._validate_creds_payload(json.dumps({"other": 1}))

    def test_rejects_non_object(self) -> None:
        with pytest.raises(RuntimeError, match="claudeAiOauth"):
            claude_auth._validate_creds_payload(json.dumps(["claudeAiOauth"]))


# ── _resolve_secret_name / _resolve_project / _resolve_batch_sa ──


class TestResolvers:
    def test_secret_name_default_uses_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(getpass, "getuser", lambda: "alice")
        assert claude_auth._resolve_secret_name(None) == "claude-code-creds-alice"

    def test_secret_name_override(self) -> None:
        assert claude_auth._resolve_secret_name("custom") == "custom"

    def test_project_from_flag(self) -> None:
        assert claude_auth._resolve_project("explicit-proj") == "explicit-proj"

    def test_project_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAPROC_GCP_PROJECT", "env-proj")
        assert claude_auth._resolve_project(None) == "env-proj"

    def test_project_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("METAPROC_GCP_PROJECT", raising=False)
        with pytest.raises(typer.BadParameter, match="METAPROC_GCP_PROJECT"):
            claude_auth._resolve_project(None)

    def test_batch_sa_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("METAPROC_GCP_SERVICE_ACCOUNT", raising=False)
        with pytest.raises(typer.BadParameter, match="METAPROC_GCP_SERVICE_ACCOUNT"):
            claude_auth._resolve_batch_sa()

    def test_batch_sa_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAPROC_GCP_SERVICE_ACCOUNT", "foo@iam")
        assert claude_auth._resolve_batch_sa() == "foo@iam"


# ── push ─────────────────────────────────────────────────────────


class TestPush:
    def test_creates_secret_grants_iam_adds_version(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        keychain_valid: None,
        _project_env: None,
    ) -> None:
        # Simulate secret not yet existing: describe fails once.
        gcloud.set_fail(("secrets", "describe"))
        result = runner.invoke(app, ["claude-auth", "push"])
        assert result.exit_code == 0, result.output
        ops = [c["args"][:2] for c in gcloud.calls]
        assert ["secrets", "describe"] in ops
        assert ["secrets", "create"] in ops
        assert ["secrets", "add-iam-policy-binding"] in ops
        assert ["secrets", "versions"] in ops
        # Version-add must go via stdin, never via file.
        version_call = next(
            c for c in gcloud.calls if c["args"][:3] == ["secrets", "versions", "add"]
        )
        assert version_call["stdin"] == VALID_PAYLOAD
        assert "--data-file=-" in version_call["args"]

    def test_skips_create_when_secret_exists(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        keychain_valid: None,
        _project_env: None,
    ) -> None:
        gcloud.set_stdout(("secrets", "describe"), "projects/x/secrets/y\n")
        result = runner.invoke(app, ["claude-auth", "push"])
        assert result.exit_code == 0, result.output
        ops = [c["args"][:2] for c in gcloud.calls]
        assert ["secrets", "create"] not in ops
        # IAM grant still happens unconditionally (Phase 0 finding).
        assert ["secrets", "add-iam-policy-binding"] in ops

    def test_iam_grant_uses_explicit_batch_sa(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        keychain_valid: None,
        _project_env: None,
    ) -> None:
        gcloud.set_stdout(("secrets", "describe"), "x")
        runner.invoke(app, ["claude-auth", "push"])
        iam_call = next(
            c for c in gcloud.calls if c["args"][:2] == ["secrets", "add-iam-policy-binding"]
        )
        assert "--member=serviceAccount:user@example.invalid" in iam_call["args"]
        assert f"--role={claude_auth.SECRET_ACCESSOR_ROLE}" in iam_call["args"]

    def test_malformed_payload_aborts_before_any_gcloud(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        monkeypatch: pytest.MonkeyPatch,
        _project_env: None,
    ) -> None:
        monkeypatch.setattr(claude_auth, "_read_keychain_blob", lambda: "not-json")
        result = runner.invoke(app, ["claude-auth", "push"])
        assert result.exit_code == 1
        assert gcloud.calls == []

    def test_missing_claudeAiOauth_aborts(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        monkeypatch: pytest.MonkeyPatch,
        _project_env: None,
    ) -> None:
        monkeypatch.setattr(claude_auth, "_read_keychain_blob", lambda: json.dumps({"other": {}}))
        result = runner.invoke(app, ["claude-auth", "push"])
        assert result.exit_code == 1
        assert gcloud.calls == []

    def test_non_darwin_aborts_before_keychain(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gcloud: _GcloudRecorder,
        _project_env: None,
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")

        def _boom() -> str:
            msg = "keychain should not be read on non-darwin"
            raise AssertionError(msg)

        monkeypatch.setattr(claude_auth, "_read_keychain_blob", _boom)
        result = runner.invoke(app, ["claude-auth", "push"])
        assert result.exit_code != 0
        assert gcloud.calls == []

    def test_custom_secret_name_flag(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        keychain_valid: None,
        _project_env: None,
    ) -> None:
        gcloud.set_stdout(("secrets", "describe"), "x")
        result = runner.invoke(app, ["claude-auth", "push", "--secret-name", "my-secret"])
        assert result.exit_code == 0, result.output
        version_call = next(
            c for c in gcloud.calls if c["args"][:3] == ["secrets", "versions", "add"]
        )
        assert "my-secret" in version_call["args"]


# ── show ─────────────────────────────────────────────────────────


class TestShow:
    def test_prints_metadata_and_iam_no_payload(
        self, gcloud: _GcloudRecorder, _project_env: None
    ) -> None:
        gcloud.set_stdout(
            ("secrets", "describe"),
            "name: projects/exampletool/secrets/claude-code-creds-alice\n",
        )
        gcloud.set_stdout(
            ("secrets", "get-iam-policy"),
            "bindings:\n- role: roles/secretmanager.secretAccessor\n",
        )
        result = runner.invoke(app, ["claude-auth", "show"])
        assert result.exit_code == 0, result.output
        assert "metadata" in result.output
        assert "IAM policy" in result.output
        # Never prints the payload — no secret version access was made.
        ops = [c["args"][:3] for c in gcloud.calls]
        assert ["secrets", "versions", "access"] not in ops
        # No access token ever appears in output.
        assert "accessToken" not in result.output
        assert "sk-ant-" not in result.output


# ── rotate ───────────────────────────────────────────────────────


class TestRotate:
    def test_destroys_prior_enabled_versions(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        keychain_valid: None,
        _project_env: None,
    ) -> None:
        gcloud.set_stdout(("secrets", "describe"), "exists")
        gcloud.set_stdout(
            ("secrets", "versions", "list"),
            "5\n4\n3\n",  # newest first
        )
        result = runner.invoke(app, ["claude-auth", "rotate"])
        assert result.exit_code == 0, result.output
        destroyed = [
            c["args"][3]
            for c in gcloud.calls
            if c["args"][:3] == ["secrets", "versions", "destroy"]
        ]
        assert destroyed == ["4", "3"]

    def test_noop_when_only_one_enabled_version(
        self,
        gcloud: _GcloudRecorder,
        darwin: None,
        keychain_valid: None,
        _project_env: None,
    ) -> None:
        gcloud.set_stdout(("secrets", "describe"), "exists")
        gcloud.set_stdout(("secrets", "versions", "list"), "1\n")
        result = runner.invoke(app, ["claude-auth", "rotate"])
        assert result.exit_code == 0, result.output
        destroyed = [c for c in gcloud.calls if c["args"][:3] == ["secrets", "versions", "destroy"]]
        assert destroyed == []
