"""Resolve Secret Manager references after a GCP Batch container starts."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from metaproc.config.env_vars import MetaprocEnv

SECRET_REFS_ENV = MetaprocEnv.METAPROC_GCP_SECRET_REFS_JSON.name
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_VERSION_PATTERN = re.compile(r"^projects/[^/]+/secrets/[^/]+/versions/[^/]+$")
_SECRET_RETRY_ATTEMPTS = 5
"""Maximum attempts for transient Secret Manager startup failures."""
_SECRET_RETRY_BASE_S = 1.0
"""Initial delay between Secret Manager retry attempts."""
_SECRET_RETRY_MAX_S = 8.0
"""Maximum delay between Secret Manager retry attempts."""

log = logging.getLogger(__name__)


def _validated_refs(
    raw: object,
    *,
    error_type: type[Exception] = RuntimeError,
) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise error_type(f"{SECRET_REFS_ENV} must be a JSON object")
    refs: dict[str, str] = {}
    for target, resource in sorted(raw.items(), key=lambda row: str(row[0])):
        if not isinstance(target, str) or _ENV_NAME_PATTERN.fullmatch(target) is None:
            raise error_type(f"{SECRET_REFS_ENV} contains an invalid env var name")
        if target == SECRET_REFS_ENV:
            raise error_type(f"{SECRET_REFS_ENV} cannot target its own contract env var")
        if not isinstance(resource, str) or _SECRET_VERSION_PATTERN.fullmatch(resource) is None:
            raise error_type(
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
    validated = _validated_refs(refs, error_type=ValueError)
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


def _with_transient_retry(operation: Callable[[], Any], *, action: str) -> Any:
    """Retry one Secret Manager startup operation on established transient failures."""
    from metaproc.cloud.gcp.batch_backend import (  # noqa: PLC0415 -- avoids import cycle
        is_transient_api_error,
    )

    for attempt in range(1, _SECRET_RETRY_ATTEMPTS + 1):
        try:
            return operation()
        except Exception as exc:
            if not is_transient_api_error(exc) or attempt == _SECRET_RETRY_ATTEMPTS:
                raise
            delay = min(
                _SECRET_RETRY_BASE_S * (2 ** (attempt - 1)),
                _SECRET_RETRY_MAX_S,
            )
            log.warning(
                "Transient Secret Manager %s failure (%s; attempt %d/%d); retrying in %.0fs",
                action,
                type(exc).__name__,
                attempt,
                _SECRET_RETRY_ATTEMPTS,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


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
        client = _with_transient_retry(
            _new_secret_manager_client,
            action="client initialization",
        )
    except ImportError as exc:
        raise RuntimeError(
            "failed to initialize Secret Manager client: ImportError; "
            "install metaproc[gcp-batch] in the agent image"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"failed to initialize Secret Manager client: {type(exc).__name__}"
        ) from exc
    hydrated: dict[str, str] = {}
    try:
        for target, resource in refs.items():
            try:
                response = _with_transient_retry(
                    lambda resource=resource: client.access_secret_version(
                        request={"name": resource}
                    ),
                    action=f"access for {target!r}",
                )
                hydrated[target] = response.payload.data.decode("utf-8")
            except Exception as exc:
                raise RuntimeError(
                    f"failed to hydrate secret env var {target!r}: {type(exc).__name__}"
                ) from exc
    finally:
        try:
            client.close()
        except Exception as exc:
            log.debug(
                "Secret Manager client close failed (%s)",
                type(exc).__name__,
            )
    target_env.update(hydrated)
    return tuple(hydrated)


__all__ = [
    "SECRET_REFS_ENV",
    "attach_secret_refs",
    "hydrate_secret_env",
    "require_secret_service_account",
]
