"""Tests for metaproc.cloud.gcp.gcp_credentials — GCP credential management."""

from __future__ import annotations

import base64
import json
import os
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.auth", reason="google-auth not installed (install metaproc[gcp])")

from metaproc.cloud.gcp.gcp_credentials import (  # noqa: E402
    _bootstrap_credentials_from_base64,
    _is_on_cloud_vm,
    get_access_token,
    get_token_expiry,
    reset,
)


@pytest.fixture(autouse=True)
def _reset_credentials():
    """Reset module-level credential state between tests."""
    reset()
    yield
    reset()


class TestGetAccessToken:
    def test_returns_token_string(self):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "ya29.test-token"
        mock_creds.expiry = datetime.now(UTC) + timedelta(minutes=30)

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default",
            return_value=(mock_creds, "project-id"),
        ):
            token = get_access_token()
            assert token == "ya29.test-token"

    def test_refreshes_when_not_valid(self):
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.token = "ya29.refreshed"
        mock_creds.expiry = datetime.now(UTC) - timedelta(minutes=5)

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default",
            return_value=(mock_creds, "project-id"),
        ):
            get_access_token()
            mock_creds.refresh.assert_called_once()

    def test_refreshes_when_near_expiry(self):
        """Token is technically valid but within the refresh margin → should refresh."""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "ya29.near-expiry"
        mock_creds.expiry = datetime.now(UTC) + timedelta(minutes=5)  # within 10-min margin

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default",
            return_value=(mock_creds, "project-id"),
        ):
            get_access_token()
            mock_creds.refresh.assert_called_once()

    def test_does_not_refresh_when_valid_with_headroom(self):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "ya29.still-good"
        mock_creds.expiry = datetime.now(UTC) + timedelta(minutes=30)

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default",
            return_value=(mock_creds, "project-id"),
        ):
            get_access_token()
            mock_creds.refresh.assert_not_called()


class TestGetTokenExpiry:
    def test_returns_expiry_datetime(self):
        expiry = datetime.now(UTC) + timedelta(minutes=30)
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "ya29.test"
        mock_creds.expiry = expiry

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default",
            return_value=(mock_creds, "project-id"),
        ):
            result = get_token_expiry()
            assert result == expiry

    def test_returns_none_when_no_expiry(self):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "ya29.test"
        mock_creds.expiry = None

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default",
            return_value=(mock_creds, "project-id"),
        ):
            result = get_token_expiry()
            assert result is None

    def test_adds_utc_to_naive_expiry(self):
        naive_expiry = datetime(2026, 4, 6, 12, 0, 0)  # noqa: DTZ001
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "ya29.test"
        mock_creds.expiry = naive_expiry

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default",
            return_value=(mock_creds, "project-id"),
        ):
            result = get_token_expiry()
            assert result is not None
            assert result.tzinfo == UTC


class TestIsOnCloudVM:
    """Tests for _is_on_cloud_vm — GCE/Batch environment detection."""

    def test_detects_batch_task_index(self):

        with patch.dict("os.environ", {"BATCH_TASK_INDEX": "0"}):
            assert _is_on_cloud_vm() is True

    def test_not_on_cloud_locally(self):

        with patch.dict("os.environ", {}, clear=True):
            assert _is_on_cloud_vm() is False

    def test_detects_mounted_filestore(self):

        with (
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
                    "METAPROC_GCP_FILESTORE_MOUNT_PATH": "/mnt/fs",
                },
            ),
            patch("pathlib.Path.is_mount", return_value=True),
        ):
            assert _is_on_cloud_vm() is True

    def test_filestore_env_but_not_mounted(self):

        with (
            patch.dict(
                "os.environ",
                {
                    "METAPROC_GCP_FILESTORE_SERVER": "10.0.0.1",
                    "METAPROC_GCP_FILESTORE_MOUNT_PATH": "/mnt/fs",
                },
            ),
            patch("pathlib.Path.is_mount", return_value=False),
        ):
            assert _is_on_cloud_vm() is False


class TestBootstrapSkipsOnCloudVM:
    """Tests that _bootstrap_credentials_from_base64 skips on cloud VMs."""

    def test_skips_base64_on_batch_vm(self, tmp_path):

        with patch.dict(
            "os.environ", {"BATCH_TASK_INDEX": "0", "GCP_CREDENTIALS_BASE64": "dGVzdA=="}
        ):
            # Remove GOOGLE_APPLICATION_CREDENTIALS if set

            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            _bootstrap_credentials_from_base64()
            # Should NOT have set GOOGLE_APPLICATION_CREDENTIALS
            assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ

    def test_uses_base64_locally(self, tmp_path):
        fake_key = json.dumps({"type": "service_account", "project_id": "test"})
        b64 = base64.b64encode(fake_key.encode()).decode()

        with (
            patch.dict("os.environ", {"GCP_CREDENTIALS_BASE64": b64}, clear=False),
            patch("tempfile.gettempdir", return_value=str(tmp_path)),
        ):
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            os.environ.pop("BATCH_TASK_INDEX", None)
            _bootstrap_credentials_from_base64()
            credential_path = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])

            assert credential_path.parent == tmp_path
            assert credential_path.name.startswith("metaproc-gcp-")
            assert stat.S_IMODE(credential_path.stat().st_mode) == 0o600
            assert json.loads(credential_path.read_text(encoding="utf-8")) == json.loads(fake_key)

            reset()
            assert not credential_path.exists()
            assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ

    def test_invalid_base64_does_not_leave_a_file(self, tmp_path):
        with (
            patch.dict("os.environ", {"GCP_CREDENTIALS_BASE64": "not-base64!"}, clear=False),
            patch("tempfile.gettempdir", return_value=str(tmp_path)),
        ):
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            os.environ.pop("BATCH_TASK_INDEX", None)

            with pytest.raises(ValueError):
                _bootstrap_credentials_from_base64()

        assert list(tmp_path.iterdir()) == []
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


class TestThreadSafety:
    def test_concurrent_callers_dont_double_init(self):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.token = "ya29.concurrent"
        mock_creds.expiry = datetime.now(UTC) + timedelta(minutes=30)
        call_count = 0
        original_return = (mock_creds, "project-id")

        def counting_default(scopes=None):
            nonlocal call_count
            call_count += 1
            return original_return

        with patch(
            "metaproc.cloud.gcp.gcp_credentials.google_auth_default", side_effect=counting_default
        ):
            threads = [threading.Thread(target=get_access_token) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert call_count == 1, f"google.auth.default() called {call_count} times, expected 1"
