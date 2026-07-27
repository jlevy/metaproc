"""Tests for the SecretRefSet typed wrapper for the GCP_SECRET_REFS cohort.

Phase 10 follow-up of the Vehicle A pool redesign.

Mirrors the AuthPoolFlags shape: SecretRef.resolve handles per-ref
env reads with plaintext-leakage refusal; SecretRefSet.to_secret_-
variables produces the Batch ``secret_variables`` mapping; .as_tuples
preserves the legacy 3-tuple shape for back-compat callers.
"""

from __future__ import annotations

import pytest

from metaproc.dispatch.secret_refs import SecretRef, SecretRefSet


class TestSecretRef:
    def test_resolve_returns_secret_when_set(self) -> None:
        ref = SecretRef(plaintext_env="X_TOKEN", secret_env="X_SECRET", description="x")
        result = ref.resolve(env={"X_SECRET": "projects/foo/secrets/bar/versions/1"})
        assert result == "projects/foo/secrets/bar/versions/1"

    def test_resolve_returns_empty_when_neither_set(self) -> None:
        ref = SecretRef(plaintext_env="X_TOKEN", secret_env="X_SECRET", description="x")
        assert ref.resolve(env={}) == ""

    def test_resolve_refuses_plaintext_leak(self) -> None:
        # Plaintext set without the SM ref → RuntimeError. The whole
        # point of the SecretRef registry is to prevent plaintext from
        # ever reaching a Batch job spec.
        ref = SecretRef(plaintext_env="X_TOKEN", secret_env="X_SECRET", description="x-token")
        with pytest.raises(RuntimeError, match="X_TOKEN.*X_SECRET.*x-token"):
            ref.resolve(env={"X_TOKEN": "plaintext-leak"})


class TestSecretRefSet:
    def test_all_known_includes_static_refs(self) -> None:
        # The three static refs (GH_TOKEN, CLAUDE_CODE_CREDS_JSON,
        # CODEX_CREDS_JSON) are pinned literally in the SecretRefSet
        # so the cohort's identity is auditable. Provider refs are
        # dynamic and depend on the registry.
        s = SecretRefSet.all_known()
        plaintext_envs = {r.plaintext_env for r in s.refs}
        assert "GH_TOKEN" in plaintext_envs
        assert "CLAUDE_CODE_CREDS_JSON" in plaintext_envs
        assert "CODEX_CREDS_JSON" in plaintext_envs

    def test_to_secret_variables_only_includes_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Set just GH_TOKEN's SM ref; the others stay unset and are
        # omitted from the resulting mapping.
        monkeypatch.setenv("METAPROC_GCP_SECRET_GH_TOKEN", "projects/p/secrets/gh/versions/1")
        monkeypatch.delenv("METAPROC_GCP_SECRET_CLAUDE_CREDS", raising=False)
        monkeypatch.delenv("METAPROC_GCP_SECRET_CODEX_CREDS", raising=False)
        # Plaintext leakage guard requires plaintext also be unset.
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_CREDS_JSON", raising=False)
        monkeypatch.delenv("CODEX_CREDS_JSON", raising=False)
        s = SecretRefSet.all_known()
        result = s.to_secret_variables()
        assert result["GH_TOKEN"] == "projects/p/secrets/gh/versions/1"
        assert "CLAUDE_CODE_CREDS_JSON" not in result
        assert "CODEX_CREDS_JSON" not in result

    def test_to_secret_variables_refuses_plaintext_leak(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "ghp_plaintext_no_secret_ref")
        monkeypatch.delenv("METAPROC_GCP_SECRET_GH_TOKEN", raising=False)
        s = SecretRefSet.all_known()
        with pytest.raises(RuntimeError, match="GH_TOKEN.*METAPROC_GCP_SECRET_GH_TOKEN"):
            s.to_secret_variables()

    def test_as_tuples_back_compat(self) -> None:
        # Legacy callers iterate (plaintext_env, secret_env, description) triples.
        s = SecretRefSet(
            refs=(
                SecretRef(plaintext_env="A_TOKEN", secret_env="A_SECRET", description="a"),
                SecretRef(plaintext_env="B_TOKEN", secret_env="B_SECRET", description="b"),
            )
        )
        assert s.as_tuples() == (
            ("A_TOKEN", "A_SECRET", "a"),
            ("B_TOKEN", "B_SECRET", "b"),
        )
