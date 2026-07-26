"""GCP credential management for Vertex MaaS.

Provides auto-refreshing access tokens using ``google.auth``.
Requires the ``gcp`` extra: ``uv sync --extra gcp``

Credential resolution order (standard ``google.auth.default()`` chain):

1. ``GOOGLE_APPLICATION_CREDENTIALS`` env var → path to SA key JSON file
2. ``GCP_CREDENTIALS_BASE64`` env var → base64-encoded SA key JSON,
   decoded to a temp file automatically (convenience for CI/.env portability)
3. GCE/GKE metadata server (auto-detected in cloud environments)
4. User credentials from ``gcloud auth application-default login``

Thread-safe: multiple concurrent callers (e.g., during batch runs) can call
``get_access_token()`` safely.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth import default as google_auth_default  # pyright: ignore[reportUnknownVariableType]
from google.auth.transport.requests import Request

from metaproc.config.env_vars import MetaprocEnv
from metaproc.settings import GCP_TOKEN_REFRESH_MARGIN_MINUTES

log = logging.getLogger(__name__)

_VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_REFRESH_MARGIN = timedelta(minutes=GCP_TOKEN_REFRESH_MARGIN_MINUTES)

_lock = threading.Lock()
_credentials: Any = None
_request: Request | None = None


def _is_on_cloud_vm() -> bool:
    """Detect if running on a GCP cloud VM (Batch worker, orchestrator, browser host).

    On cloud VMs, the attached service account provides credentials via ADC
    (metadata server). We should NOT use ``GCP_CREDENTIALS_BASE64`` there — it
    may be stale or absent, and ADC is the correct auth path.
    """
    # GCP Batch sets BATCH_TASK_INDEX on all worker/orchestrator containers.
    if MetaprocEnv.BATCH_TASK_INDEX.read_str(default=None) is not None:
        return True
    # Filestore mount path set → likely a cloud VM with Filestore mounted.
    if MetaprocEnv.METAPROC_GCP_FILESTORE_SERVER.read_str(default=None):
        mount_path = MetaprocEnv.METAPROC_GCP_FILESTORE_MOUNT_PATH.read_str(
            default="/mnt/filestore"
        )
        if Path(mount_path).is_mount():
            return True
    return False


def _bootstrap_credentials_from_base64() -> None:
    """Decode ``GCP_CREDENTIALS_BASE64`` to a temp file and set ``GOOGLE_APPLICATION_CREDENTIALS``.

    Follows the same pattern as an external tool's ``setup-gcp-credentials.sh``:
    the SA key JSON is stored base64-encoded in an env var (or ``.env`` file),
    decoded to a temp file at runtime, and pointed to by the standard
    ``GOOGLE_APPLICATION_CREDENTIALS`` env var. Works in local dev, CI, and containers.

    Skipped on cloud VMs where ADC via attached service account is preferred.
    """
    if MetaprocEnv.GOOGLE_APPLICATION_CREDENTIALS.read_str(default=None):
        return  # already set — nothing to do
    if _is_on_cloud_vm():
        log.debug("Running on cloud VM — skipping GCP_CREDENTIALS_BASE64, using ADC")
        return
    b64 = MetaprocEnv.GCP_CREDENTIALS_BASE64.read_str(default="")
    if not b64:
        return
    key_dir = Path(tempfile.gettempdir()) / "gcp" / "keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "sa-key.json"
    key_file.write_bytes(base64.b64decode(b64))
    _ = key_file.chmod(0o600)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_file)
    log.debug("Decoded GCP_CREDENTIALS_BASE64 to %s", key_file)


def _token_fingerprint(token: str) -> str:
    """First 8 hex chars of SHA-256 hash — enough to correlate tokens in logs."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def _ensure_credentials() -> Any:
    """Lazily initialize credentials on first call."""
    global _credentials, _request  # noqa: PLW0603
    if _credentials is None:
        _bootstrap_credentials_from_base64()
        _credentials, _ = google_auth_default(scopes=_VERTEX_SCOPES)  # pyright: ignore[reportUnknownMemberType]
        _request = Request()
        log.info("GCP credentials initialized: %s", type(_credentials).__name__)
    return _credentials


def get_access_token() -> str:
    """Return a valid access token, refreshing if expired.

    Thread-safe. Raises ``google.auth.exceptions.DefaultCredentialsError``
    if no credentials are available.
    """
    with _lock:
        creds = _ensure_credentials()
        assert _request is not None
        needs_refresh: bool = not creds.valid
        if not needs_refresh and creds.expiry is not None:
            expiry_utc: datetime = creds.expiry
            if expiry_utc.tzinfo is None:
                expiry_utc = expiry_utc.replace(tzinfo=UTC)
            needs_refresh = (expiry_utc - datetime.now(UTC)) < _REFRESH_MARGIN
        if needs_refresh:
            log.info("GCP token expired or near-expiry, refreshing")
            creds.refresh(_request)
            token: str = str(creds.token)
            expiry = creds.expiry
            log.info(
                "GCP token refreshed: fingerprint=%s, expiry=%s",
                _token_fingerprint(token),
                expiry,
            )
            return token
        return str(creds.token)


def get_token_expiry() -> datetime | None:
    """Return the token's expiry time, or None if unknown."""
    with _lock:
        creds = _ensure_credentials()
        if hasattr(creds, "expiry") and creds.expiry is not None:
            exp: datetime = creds.expiry
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            return exp
        return None


def reset() -> None:
    """Reset cached credentials. Useful for testing."""
    global _credentials, _request  # noqa: PLW0603
    with _lock:
        _credentials = None
        _request = None
