"""Tests for ``for_each.key`` field and item-key resolution."""

from __future__ import annotations

import logging

import pytest

from metaproc.engine.pathing import _KEY_DEFAULT_WARNED, resolve_item_key
from metaproc.models.authored import ForEach
from metaproc.paths import ITEM_KEY_RE, is_safe_item_key


class TestForEachKeyValidation:
    def test_template_with_placeholder_only(self):
        fe = ForEach(over="deps.tickers", bind="ticker", key="{{ticker}}")
        assert fe.key == "{{ticker}}"

    def test_template_with_safe_chars(self):
        fe = ForEach(over="deps.tickers", bind="ticker", key="prefix_{{ticker}}-v1")
        assert fe.key == "prefix_{{ticker}}-v1"

    def test_template_with_dot_separator(self):
        fe = ForEach(over="deps.x", bind="x", key="{{ticker}}.{{date}}")
        assert fe.key == "{{ticker}}.{{date}}"

    def test_unsafe_chars_rejected(self):
        with pytest.raises(ValueError, match=r"\[A-Za-z0-9\._-\]"):
            ForEach(over="deps.x", bind="x", key="bad/key")

    def test_space_rejected(self):
        with pytest.raises(ValueError, match=r"\[A-Za-z0-9\._-\]"):
            ForEach(over="deps.x", bind="x", key="bad key")

    def test_omitted_key_is_none(self):
        """An omitted ``key:`` parses as ``None``; the default fills at runtime."""
        fe = ForEach(over="deps.tickers", bind="ticker")
        assert fe.key is None


class TestItemKeyRegex:
    def test_safe(self):
        for v in ("AAPL", "tic.ker", "tic-ker", "tic_ker", "v1.2.3", "MSFT__2026"):
            assert is_safe_item_key(v), v

    def test_unsafe(self):
        for v in ("a/b", "a b", "a:b", "", "a/", "a\\b"):
            assert not is_safe_item_key(v), v

    def test_regex_pattern(self):
        assert ITEM_KEY_RE.pattern == r"[A-Za-z0-9._\-]+"


class TestResolveItemKey:
    def setup_method(self):
        _KEY_DEFAULT_WARNED.clear()

    def test_resolves_explicit_key(self):
        fe = ForEach(over="deps.tickers", bind="ticker", key="{{ticker}}")
        assert resolve_item_key(fe, {"ticker": "MSFT"}, "predict") == "MSFT"

    def test_resolves_composite_key(self):
        fe = ForEach(over="deps.x", bind="x", key="{{ticker}}__{{model}}")
        resolved = resolve_item_key(fe, {"ticker": "MSFT", "model": "claude-opus-4-7"}, "step1")
        assert resolved == "MSFT__claude-opus-4-7"

    def test_omitted_key_defaults_to_bind_with_warning(self, caplog):
        fe = ForEach(over="deps.tickers", bind="ticker")
        with caplog.at_level(logging.WARNING, logger="metaproc.engine.pathing"):
            resolved = resolve_item_key(fe, {"ticker": "MSFT"}, "predict")
        assert resolved == "MSFT"
        assert any("for_each.key omitted" in rec.message for rec in caplog.records)

    def test_default_warning_dedup_per_step(self, caplog):
        fe = ForEach(over="deps.tickers", bind="ticker")
        with caplog.at_level(logging.WARNING, logger="metaproc.engine.pathing"):
            resolve_item_key(fe, {"ticker": "MSFT"}, "predict")
            resolve_item_key(fe, {"ticker": "AAPL"}, "predict")
        warnings = [r for r in caplog.records if "for_each.key omitted" in r.message]
        assert len(warnings) == 1

    def test_unresolved_template_raises(self):
        fe = ForEach(over="deps.x", bind="ticker", key="{{ticker}}")
        with pytest.raises(ValueError, match="did not fully resolve"):
            resolve_item_key(fe, {}, "step1")

    def test_unsafe_resolved_value_raises(self):
        """Resolved value with disallowed character must be rejected."""
        fe = ForEach(over="deps.x", bind="ticker", key="{{ticker}}")
        with pytest.raises(ValueError, match="unsafe path component"):
            resolve_item_key(fe, {"ticker": "bad/value"}, "step1")
