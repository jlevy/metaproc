"""Resolve Secret Manager references after a GCP Batch container starts."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

from metaproc.config.env_vars import MetaprocEnv

SECRET_REFS_ENV = MetaprocEnv.METAPROC_GCP_SECRET_REFS_JSON.name
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_VERSION_PATTERN = re.compile(r"^projects/[^/]+/secrets/[^/]+/versions/[^/]+$")


def _validated_refs(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise RuntimeError(f"{SECRET_REFS_ENV} must be a JSON object")
    refs: dict[str, str] = {}
    for target, resource in sorted(raw.items(), key=lambda row: str(row[0])):
        if not isinstance(target, str) or _ENV_NAME_PATTERN.fullmatch(target) is None:
            raise RuntimeError(f"{SECRET_REFS_ENV} contains an invalid env var name")
        if target == SECRET_REFS_ENV:
            raise RuntimeError(f"{SECRET_REFS_ENV} cannot target its own contract env var")
        if not isinstance(resource, str) or _SECRET_VERSION_PATTERN.fullmatch(resource) is None:
            raise RuntimeError(
                f"{SECRET_REFS_ENV} entry {target!r} must name a Secret Manager version"
            )
        refs[target] = resource
    return refs


def attach_secret_refs(env: MutableMapping[str, str], refs: Mapping[str, str]) -> None:
    """Attach resource references to a Batch env without attaching secret values."""
    if not refs:
        return
    if SECRET_REFS_ENV in env:
        raise ValueError(f"dispatcher env already contains reserved key {SECRET_REFS_ENV}")
    validated = _validated_refs(refs)
    conflicts = sorted(target for target in validated if env.get(target))
    if conflicts:
        raise ValueError(f"secret target already exists in Batch env: {conflicts}")
    env[SECRET_REFS_ENV] = json.dumps(validated, sort_keys=True, separators=(",", ":"))


def require_secret_service_account(refs: Mapping[str, str], service_account_email: str) -> None:
    """Require an operator-selected runtime identity for secret hydration."""
    if refs and not service_account_email:
        raise ValueError(
            "Set METAPROC_GCP_SERVICE_ACCOUNT before binding Secret Manager secrets "
            "to a GCP Batch run"
        )


def _new_secret_manager_client() -> Any:
    from google.cloud import secretmanager  # noqa: PLC0415 -- optional GCP extra

    return secretmanager.SecretManagerServiceClient()


def hydrate_secret_env(env: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """Fetch every dispatched secret atomically into the current process env."""
    target_env = os.environ if env is None else env
    encoded = target_env.get(SECRET_REFS_ENV, "")
    if not encoded:
        return ()
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{SECRET_REFS_ENV} is not valid JSON") from exc
    refs = _validated_refs(decoded)
    if not refs:
        return ()
    conflicts = sorted(target for target in refs if target_env.get(target))
    if conflicts:
        raise RuntimeError(f"secret target env var already exists before hydration: {conflicts}")

    try:
        client = _new_secret_manager_client()
    except Exception as exc:
        raise RuntimeError("failed to initialize Secret Manager client") from exc
    hydrated: dict[str, str] = {}
    try:
        for target, resource in refs.items():
            try:
                response = client.access_secret_version(request={"name": resource})
                hydrated[target] = response.payload.data.decode("utf-8")
            except Exception as exc:
                raise RuntimeError(f"failed to hydrate secret env var {target!r}") from exc
    finally:
        try:
            client.close()
        except Exception:
            pass
    target_env.update(hydrated)
    return tuple(hydrated)


__all__ = [
    "SECRET_REFS_ENV",
    "attach_secret_refs",
    "hydrate_secret_env",
    "require_secret_service_account",
]
