"""Tests for monitor utilities."""

from __future__ import annotations

from metaproc.runpool.monitor import (
    check_descendants_limit,
    check_log_limit,
    check_rss_limit,
)


class TestHealthChecks:
    def test_rss_limit_exceeded(self):
        assert check_rss_limit(5000, 4000) is True

    def test_rss_limit_ok(self):
        assert check_rss_limit(3000, 4000) is False

    def test_rss_limit_none_values(self):
        assert check_rss_limit(None, 4000) is False
        assert check_rss_limit(3000, None) is False

    def test_log_limit_exceeded(self):
        assert check_log_limit(10000, 5000) is True

    def test_log_limit_ok(self):
        assert check_log_limit(1000, 5000) is False

    def test_descendants_limit_exceeded(self):
        assert check_descendants_limit(10, 5) is True

    def test_descendants_limit_ok(self):
        assert check_descendants_limit(3, 5) is False
