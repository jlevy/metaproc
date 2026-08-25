"""Typed registry for cloud-runtime Secret Manager references.

Provider-specific references flow through
:func:`metaproc.config.providers.gcp_secret_refs`; infrastructure and CLI
credentials are declared alongside them in :class:`SecretRefSet`.

Usage:

.. code-block:: python

    from metaproc.dispatch.secret_refs import SecretRefSet

    refs = SecretRefSet.all_known()
    secrets = refs.resolve_env_refs()  # plaintext_env -> SM resource name
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from metaproc.config.providers import gcp_secret_refs


@dataclass(frozen=True)
class SecretRef:
    """One GCP Secret Manager binding for a container runtime env var.

    ``plaintext_env`` is the env var the worker subprocess reads (e.g.
    ``GH_TOKEN``). ``secret_env`` is the operator-side ref env var (e.g.
    ``METAPROC_GCP_SECRET_GH_TOKEN``) whose value is the SM resource
    name (``projects/.../secrets/.../versions/...``). ``description``
    is the human label used in plaintext-leak refusal messages.
    """

    plaintext_env: str
    secret_env: str
    description: str

    def resolve(self, env: Mapping[str, str] | None = None) -> str:
        """Return the SM resource name from ``secret_env``, or ``""`` when unset.

        Refuses plaintext leakage: if ``plaintext_env`` is set without
        ``secret_env``, raises ``RuntimeError`` so the dispatch chain
        fails fast rather than silently submitting a cloud job whose
        env vars contain a plaintext credential. Returns ``""`` when
        neither is set (the credential simply isn't in scope for this
        dispatch).
        """

        e: Mapping[str, str] = env if env is not None else os.environ
        secret = e.get(self.secret_env, "")
        if secret:
            return secret
        if e.get(self.plaintext_env, ""):
            msg = (
                f"{self.plaintext_env} is set but {self.secret_env} is not. "
                f"Refusing to leak {self.description} into Batch job env. "
                "See metaproc credential setup docs."
            )
            raise RuntimeError(msg)
        return ""


@dataclass(frozen=True)
class SecretRefSet:
    """Ordered collection of :class:`SecretRef` bindings.

    Mirrors the :class:`AuthPoolFlags` shape (``from_env`` / ``to_env_vars``
    semantics, but for SM refs):

    - :meth:`all_known` composes the static non-provider refs with the
      provider-derived ones (model API keys whose Batch path is SM-backed).
    - :meth:`resolve_env_refs` returns ``plaintext_env → SM resource
      name`` for the container-side hydration contract.
    """

    refs: tuple[SecretRef, ...]

    @classmethod
    def all_known(cls) -> SecretRefSet:
        """Construct the canonical full set: static refs + provider refs.

        The static refs (CLI OAuth blobs, infra tokens) are kept inline
        here so the cohort lives in one place; the provider refs are
        aggregated from :func:`metaproc.config.providers.gcp_secret_refs`
        so adding a new pi-cli provider needs no edits to this module.
        """

        static: tuple[SecretRef, ...] = (
            SecretRef(
                plaintext_env="GH_TOKEN",
                secret_env="METAPROC_GCP_SECRET_GH_TOKEN",
                description="plaintext GitHub token",
            ),
            SecretRef(
                plaintext_env="CLAUDE_CODE_CREDS_JSON",
                secret_env="METAPROC_GCP_SECRET_CLAUDE_CREDS",
                description="Claude Code OAuth blob",
            ),
            SecretRef(
                plaintext_env="CODEX_CREDS_JSON",
                secret_env="METAPROC_GCP_SECRET_CODEX_CREDS",
                description="Codex CLI ChatGPT-OAuth blob",
            ),
        )
        provider_refs = tuple(
            SecretRef(plaintext_env=p, secret_env=s, description=d)
            for (p, s, d) in gcp_secret_refs()
        )
        return cls(refs=static + provider_refs)

    def resolve_env_refs(self, env: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return ``plaintext_env → SM resource name`` for refs that resolve.

        Skips refs whose ``secret_env`` isn't set (and whose
        ``plaintext_env`` also isn't — those raise via
        :meth:`SecretRef.resolve`). The returned dict contains resource
        references only; the container fetches their values after startup.
        """
        out: dict[str, str] = {}
        for ref in self.refs:
            resolved = ref.resolve(env)
            if resolved:
                out[ref.plaintext_env] = resolved
        return out


__all__ = ["SecretRef", "SecretRefSet"]
