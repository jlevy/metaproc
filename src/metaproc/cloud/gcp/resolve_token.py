"""Resolve a GCP access token.

Uses ``google.auth`` for auto-refreshing credentials when ``metaproc[gcp]``
is installed. Fails clearly if the extra is not installed — there is no
degraded fallback path.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_GCP_INSTALL_HINT = (
    "GCP auth requires the gcp extra: uv sync --extra gcp (or uv pip install metaproc[gcp])"
)


def resolve_gcp_token() -> str:
    """Return a valid GCP access token.

    Uses google.auth credentials with automatic refresh.
    Raises ``ImportError`` if ``metaproc[gcp]`` is not installed.
    Raises ``google.auth.exceptions.DefaultCredentialsError`` if no
    credentials are configured.
    """
    try:
        from metaproc.cloud.gcp.gcp_credentials import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            get_access_token,
            get_token_expiry,
        )
    except ImportError:
        raise ImportError(_GCP_INSTALL_HINT) from None

    token = get_access_token()
    expiry = get_token_expiry()
    log.debug("GCP token resolved (expiry: %s)", expiry)
    return token
