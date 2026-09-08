"""Tests for metaproc codex-auth command (the selector and fallback regressions)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from metaproc.adapters.codex_cli import resolve_codex_home
from metaproc.commands import codex_auth

_VALID_AUTH_JSON = json.dumps(
    {
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "fake-id",
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "account_id": "fake-account",
            "auth_mode": "chatgpt",
        },
        "last_refresh": "2026-04-24T00:00:00Z",
    }
)


class TestCodexHomeResolution:
    """Per-review: codex_auth and the adapter must agree on CODEX_HOME."""

    def test_codex_home_delegates_to_shared_resolver(self, tmp_path: Path, monkeypatch):
        """_codex_home must resolve via the same path the adapter uses."""

        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "alt"))
        assert codex_auth._codex_home() == resolve_codex_home()
        assert codex_auth._codex_home() == tmp_path / "alt"

    def test_codex_home_falls_back_to_home_dotcodex(self, tmp_path: Path, monkeypatch):
        """When CODEX_HOME is unset, both surfaces should default to ~/.codex."""

        monkeypatch.delenv("CODEX_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert codex_auth._codex_home() == resolve_codex_home() == tmp_path / ".codex"


class TestPreflightCheckConfigStore:
    """Regression coverage: fail hard if cli_auth_credentials_store != 'file'."""

    def test_missing_config_toml_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        # Config file doesn't exist.
        with pytest.raises(RuntimeError, match="does not exist"):
            codex_auth._preflight_check_config_store()

    def test_auto_default_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text("# codex config\n")
        with pytest.raises(RuntimeError, match="cli_auth_credentials_store"):
            codex_auth._preflight_check_config_store()

    def test_explicit_auto_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "auto"\n')
        with pytest.raises(RuntimeError, match="auto"):
            codex_auth._preflight_check_config_store()

    def test_keychain_value_raises(self, tmp_path: Path, monkeypatch):
        """macOS Keychain store — correctly refused."""
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "keychain"\n')
        with pytest.raises(RuntimeError, match="keychain"):
            codex_auth._preflight_check_config_store()

    def test_file_store_passes(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        # No exception.
        codex_auth._preflight_check_config_store()

    def test_file_store_with_other_keys_passes(self, tmp_path: Path, monkeypatch):
        """Extra keys and comments don't break the parser."""
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text(
            "# Codex config\n"
            'model_provider = "openai"\n'
            'cli_auth_credentials_store = "file"  # required for headless push\n'
            "[tui]\n"
            "disable_foo = true\n"
        )
        codex_auth._preflight_check_config_store()

    def test_table_header_does_not_create_false_positive(self, tmp_path: Path, monkeypatch):
        """Per-review: a non-root cli_auth_credentials_store key must NOT pass.

        Only a root-table key counts. A `cli_auth_credentials_store = "file"`
        nested under a `[some_section]` header should leave the root unset
        and trip the preflight.
        """
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text(
            '[some_section]\ncli_auth_credentials_store = "file"\n'
        )
        with pytest.raises(RuntimeError, match="cli_auth_credentials_store"):
            codex_auth._preflight_check_config_store()

    def test_invalid_toml_does_not_silently_pass(self, tmp_path: Path, monkeypatch):
        """Malformed config.toml must not silently pretend the key is set."""
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text("this is = not valid toml [[[\n")
        with pytest.raises(RuntimeError, match="cli_auth_credentials_store"):
            codex_auth._preflight_check_config_store()


class TestValidateCodexPayload:
    def test_valid_chatgpt_payload_passes(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        codex_auth._validate_codex_payload(_VALID_AUTH_JSON)

    def test_invalid_json_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="not valid JSON"):
            codex_auth._validate_codex_payload("{not json")

    def test_non_object_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="JSON object"):
            codex_auth._validate_codex_payload('"a string"')

    def test_missing_tokens_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="tokens"):
            codex_auth._validate_codex_payload(json.dumps({"no_tokens": True}))

    def test_apikey_mode_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        payload = json.dumps({"tokens": {"auth_mode": "apikey", "access_token": "sk-..."}})
        with pytest.raises(RuntimeError, match="chatgpt|apikey"):
            codex_auth._validate_codex_payload(payload)


class TestPushCore:
    """Full push flow with monkey-patched gcloud + Keychain."""

    def _setup_config(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        (tmp_path / "auth.json").write_text(_VALID_AUTH_JSON)

    def test_push_core_happy_path(self, tmp_path: Path, monkeypatch):
        self._setup_config(tmp_path, monkeypatch)

        calls: list[list[str]] = []

        def _fake_gcloud(args: list[str], *, stdin: str | None = None, capture: bool = True):
            calls.append(args)
            if args[:2] == ["secrets", "describe"]:
                # First check on secret existence: pretend it doesn't exist.
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch.object(codex_auth, "_secret_exists", return_value=False):
            with patch.object(codex_auth, "_gcloud", side_effect=_fake_gcloud):
                codex_auth._push_core("proj-x", "codex-creds-alice", "user@example.invalid")

        # Validate the push sequence hit create_secret, grant IAM, and add_version.
        cmds = [args[0:3] for args in calls]
        assert ["secrets", "create", "codex-creds-alice"] in cmds
        assert ["secrets", "add-iam-policy-binding", "codex-creds-alice"] in cmds
        assert ["secrets", "versions", "add"] in cmds

    def test_push_core_skips_create_when_secret_exists(self, tmp_path: Path, monkeypatch):
        self._setup_config(tmp_path, monkeypatch)

        calls: list[list[str]] = []

        def _fake_gcloud(args: list[str], *, stdin: str | None = None, capture: bool = True):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch.object(codex_auth, "_secret_exists", return_value=True):
            with patch.object(codex_auth, "_gcloud", side_effect=_fake_gcloud):
                codex_auth._push_core("proj-x", "codex-creds-alice", "user@example.invalid")

        cmds = [args[0:3] for args in calls]
        # Existing secret → no create call.
        assert ["secrets", "create", "codex-creds-alice"] not in cmds
        # But IAM grant + version still fire.
        assert ["secrets", "add-iam-policy-binding", "codex-creds-alice"] in cmds
        assert ["secrets", "versions", "add"] in cmds

    def test_push_core_fails_fast_on_preflight(self, tmp_path: Path, monkeypatch):
        """Preflight fires before gcloud is touched."""
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        # Deliberately leave config.toml absent.

        with patch.object(codex_auth, "_gcloud") as gcloud_mock:
            with pytest.raises(RuntimeError, match="cli_auth_credentials_store|does not exist"):
                codex_auth._push_core("proj-x", "codex-creds-alice", "user@example.invalid")
            gcloud_mock.assert_not_called()

    def test_push_core_fails_fast_on_missing_auth_json(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        # auth.json absent.

        with patch.object(codex_auth, "_gcloud") as gcloud_mock:
            with pytest.raises(RuntimeError, match="auth.json"):
                codex_auth._push_core("proj-x", "codex-creds-alice", "user@example.invalid")
            gcloud_mock.assert_not_called()

    def test_push_core_validates_chatgpt_mode_before_push(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        (tmp_path / "auth.json").write_text(
            json.dumps({"tokens": {"auth_mode": "apikey", "access_token": "sk-..."}})
        )

        with patch.object(codex_auth, "_gcloud") as gcloud_mock:
            with pytest.raises(RuntimeError, match="apikey|chatgpt"):
                codex_auth._push_core("proj-x", "codex-creds-alice", "user@example.invalid")
            gcloud_mock.assert_not_called()


class TestSecretNameDefault:
    def test_default_secret_name_is_codex_creds_user(self, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "alice")
        assert codex_auth._resolve_secret_name(None) == "codex-creds-alice"

    def test_explicit_secret_name_is_respected(self, monkeypatch):
        assert codex_auth._resolve_secret_name("custom") == "custom"
