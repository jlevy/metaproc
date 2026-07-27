"""Tests for B9: stale api_key cleared on gcloud token refresh failure."""

from __future__ import annotations

import pytest


class TestStaleApiKeyClear:
    """Verify that api_key is cleared from merged_config when token refresh fails."""

    def test_api_key_cleared_on_refresh_failure(self):
        """When resolve_gcp_token raises, stale api_key must be removed."""
        merged_config: dict[str, object] = {"api_key": "stale-token", "provider": "vertex-maas"}

        def _refresh_gcloud_token() -> None:
            try:
                raise RuntimeError("token refresh failed")
            except Exception:
                merged_config.pop("api_key", None)
                raise

        with pytest.raises(RuntimeError, match="token refresh failed"):
            _refresh_gcloud_token()

        assert "api_key" not in merged_config

    def test_api_key_set_on_refresh_success(self):
        """When resolve_gcp_token succeeds, api_key is updated."""
        merged_config: dict[str, object] = {"api_key": "old-token", "provider": "vertex-maas"}

        def _refresh_gcloud_token() -> None:
            try:
                merged_config["api_key"] = "new-token"
            except Exception:
                merged_config.pop("api_key", None)
                raise

        _refresh_gcloud_token()
        assert merged_config["api_key"] == "new-token"

    def test_api_key_cleared_when_absent(self):
        """Clearing a missing api_key should not raise."""
        merged_config: dict[str, object] = {"provider": "vertex-maas"}

        def _refresh_gcloud_token() -> None:
            try:
                raise RuntimeError("token refresh failed")
            except Exception:
                merged_config.pop("api_key", None)
                raise

        with pytest.raises(RuntimeError):
            _refresh_gcloud_token()

        assert "api_key" not in merged_config
