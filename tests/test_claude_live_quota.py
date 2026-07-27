"""Unit tests for the Claude Code live quota probe.

Covers the pure parsing helpers (``_normalize_oauth_usage``,
``_extract_oauth_access_token``) directly without hitting the network.
The HTTP fetch path is tested via monkeypatching ``urllib.request.urlopen``.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime as _dt
from email.message import Message
from pathlib import Path

import pytest

from metaproc.adapters import claude_code
from metaproc.adapters.base import QuotaUsage
from metaproc.dispatch.credential_pool import Vehicle


def test_extract_access_token_happy_path(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat-secret-token",
                    "refreshToken": "sk-ant-ort-secret-refresh",
                }
            }
        )
    )
    assert claude_code._extract_oauth_access_token(creds) == "sk-ant-oat-secret-token"


def test_extract_access_token_missing_file(tmp_path: Path) -> None:
    assert claude_code._extract_oauth_access_token(tmp_path / "no-such") is None


def test_extract_access_token_malformed_json(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text("{this is not json")
    assert claude_code._extract_oauth_access_token(creds) is None


def test_extract_access_token_missing_oauth_envelope(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"someOtherKey": "value"}))
    assert claude_code._extract_oauth_access_token(creds) is None


def test_normalize_oauth_usage_ratio_scale() -> None:
    """0.0-1.0 utilization (community example A)."""
    payload = {
        "five_hour": {"utilization": 0.67, "resets_at": "2026-05-13T10:10:00Z"},
        "seven_day": {"utilization": 0.30, "resets_at": "2026-05-20T00:00:00Z"},
    }
    q = claude_code._normalize_oauth_usage(payload)
    assert q is not None
    # 5h is more-binding at 67% utilization, so 33% remaining.
    assert q.remaining_ratio == pytest.approx(0.33, abs=0.001)
    assert q.unit_kind == "window-utilization-live"
    assert "five_hour" in q.detail
    assert "seven_day" in q.detail


def test_normalize_oauth_usage_percent_scale() -> None:
    """0-100 utilization (community example B)."""
    payload = {
        "five_hour": {"utilization": 35.0, "resets_at": "2026-05-13T10:10:00Z"},
        "seven_day": {"utilization": 6.0, "resets_at": "2026-05-20T00:00:00Z"},
    }
    q = claude_code._normalize_oauth_usage(payload)
    assert q is not None
    # 35% util → 65% remaining.
    assert q.remaining_ratio == pytest.approx(0.65, abs=0.001)


def test_normalize_oauth_usage_picks_more_binding_window() -> None:
    """When 7d is closer to its cap than 5h, the 7d window binds."""
    payload = {
        "five_hour": {"utilization": 0.20, "resets_at": "2026-05-13T10:10:00Z"},
        "seven_day": {"utilization": 0.85, "resets_at": "2026-05-20T00:00:00Z"},
    }
    q = claude_code._normalize_oauth_usage(payload)
    assert q is not None
    assert q.remaining_ratio == pytest.approx(0.15, abs=0.001)


def test_normalize_oauth_usage_clamps_overflow() -> None:
    """A reported util > 100% (percent scale) is clamped at fully-exhausted.
    Values 1.0 < x ≤ 100.0 are treated as percents per the scale heuristic;
    values > 100.0 still clamp to 1.0 ratio (fully exhausted)."""
    payload = {"five_hour": {"utilization": 110.0, "resets_at": "2026-05-13T10:10:00Z"}}
    q = claude_code._normalize_oauth_usage(payload)
    assert q is not None
    assert q.remaining_ratio == 0.0


def test_normalize_oauth_usage_missing_windows() -> None:
    assert claude_code._normalize_oauth_usage({}) is None
    assert claude_code._normalize_oauth_usage({"five_hour": "not a dict"}) is None
    assert claude_code._normalize_oauth_usage(None) is None


def test_normalize_oauth_usage_resets_at_parses_iso_to_epoch() -> None:
    payload = {"five_hour": {"utilization": 0.5, "resets_at": "2026-05-13T10:10:00Z"}}
    q = claude_code._normalize_oauth_usage(payload)
    assert q is not None
    assert q.resets_at is not None

    expected = int(_dt.fromisoformat("2026-05-13T10:10:00+00:00").timestamp())
    assert q.resets_at == expected


def test_query_anthropic_oauth_usage_swallows_network_errors(monkeypatch) -> None:
    """Any urlopen failure returns None — quota probe must never raise."""

    def _boom(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert claude_code._query_anthropic_oauth_usage("token") is None


def test_query_anthropic_oauth_usage_scope_error_is_actionable(monkeypatch) -> None:
    """The live quota endpoint requires user:profile; stale setup-tokens
    should render an actionable detail instead of a silent dash."""

    def _boom(req, timeout: float = 0):  # noqa: ARG001
        body = (
            b'{"type":"error","error":{"message":'
            b'"OAuth token does not meet scope requirement user:profile"}}'
        )
        raise urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            hdrs=Message(),
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    q = claude_code._query_anthropic_oauth_usage("token")
    assert q is not None
    assert q.remaining_ratio is None
    assert q.unit_kind == "unavailable"
    assert "user:profile" in q.detail


def test_query_anthropic_oauth_usage_rate_limit_is_actionable(monkeypatch) -> None:
    """429 from the quota endpoint should not collapse into a silent dash."""

    def _boom(req, timeout: float = 0):  # noqa: ARG001
        body = b'{"error":{"type":"rate_limit_error","message":"Rate limited"}}'
        raise urllib.error.HTTPError(
            req.full_url,
            429,
            "Too Many Requests",
            hdrs=Message(),
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    q = claude_code._query_anthropic_oauth_usage("token")
    assert q is not None
    assert q.remaining_ratio is None
    assert q.unit_kind == "unavailable"
    assert "rate-limited" in q.detail


def test_query_anthropic_oauth_usage_happy_path(monkeypatch) -> None:
    """A successful urlopen flows through _normalize_oauth_usage."""

    class _FakeResp:
        def __init__(self, body: str) -> None:
            self._buf = io.BytesIO(body.encode())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return self._buf.getvalue()

    payload_str = json.dumps(
        {
            "five_hour": {"utilization": 0.50, "resets_at": "2026-05-13T10:10:00Z"},
            "seven_day": {"utilization": 0.30, "resets_at": "2026-05-20T00:00:00Z"},
        }
    )

    def _fake_urlopen(req, timeout: float = 0):  # noqa: ARG001
        # Verify the request carries the right headers / URL.
        assert req.full_url == "https://api.anthropic.com/api/oauth/usage"
        assert req.headers["Authorization"] == "Bearer test-token"
        assert req.headers["Anthropic-beta"] == "oauth-2025-04-20"
        return _FakeResp(payload_str)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    q = claude_code._query_anthropic_oauth_usage("test-token")
    assert q is not None
    assert isinstance(q, QuotaUsage)
    assert q.remaining_ratio == pytest.approx(0.50, abs=0.001)


def test_query_live_quota_returns_none_without_credentials(tmp_path: Path) -> None:
    """If the slot has no .credentials.json, return None silently."""
    adapter = claude_code.ClaudeCodeCliAdapter()
    assert adapter.query_live_quota(tmp_path) is None


def test_query_live_quota_e2e(monkeypatch, tmp_path: Path) -> None:
    """End-to-end: credential file → access token → HTTP → QuotaUsage."""

    class _FakeResp:
        def __init__(self, body: str) -> None:
            self._body = body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return self._body

    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "live-token"}}))
    payload_str = json.dumps(
        {"five_hour": {"utilization": 0.10, "resets_at": "2026-05-13T10:10:00Z"}}
    )

    def _fake_urlopen(req, timeout: float = 0):  # noqa: ARG001
        assert req.headers["Authorization"] == "Bearer live-token"
        return _FakeResp(payload_str)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    adapter = claude_code.ClaudeCodeCliAdapter()
    q = adapter.query_live_quota(tmp_path)
    assert q is not None
    assert q.remaining_ratio == pytest.approx(0.90, abs=0.001)


def test_query_live_quota_vehicle_a_uses_pool_blob(monkeypatch, tmp_path: Path) -> None:
    """Vehicle A stores the static bearer in the pool blob and writes no
    .credentials.json, so the live quota probe must use that blob directly."""
    seen: dict[str, str] = {}

    def _fake_query(access_token: str) -> QuotaUsage:
        seen["access_token"] = access_token
        return QuotaUsage(
            remaining_ratio=0.75,
            remaining_units=None,
            total_units=None,
            resets_at=None,
            unit_kind="window-utilization-live",
            detail="fake",
        )

    monkeypatch.setattr(claude_code, "_query_anthropic_oauth_usage", _fake_query)

    adapter = claude_code.ClaudeCodeCliAdapter()
    q = adapter.query_live_quota(
        tmp_path,
        vehicle=Vehicle.OAUTH_TOKEN,
        blob="  sk-ant-oat01-vehicle-a-token  ",
    )

    assert q is not None
    assert q.remaining_ratio == pytest.approx(0.75)
    assert seen["access_token"] == "sk-ant-oat01-vehicle-a-token"
