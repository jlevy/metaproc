"""Tests for schema token parse/format/round-trip and registry."""

from __future__ import annotations

import pytest

from metaproc.io.frontmatter import get_registered_envelopes
from metaproc.io.schema_token import (
    SchemaToken,
    _build_schema_registry,
    format_schema_token,
    parse_schema_token,
    resolve_schema,
)
from metaproc.models.plan import Plan
from metaproc.models.usage import UsageReport


class TestParseSchemaToken:
    def test_valid_token(self) -> None:
        result = parse_schema_token("example_workflow:RecordDocument/0.1")
        assert result == SchemaToken("example_workflow", "RecordDocument", "0.1")

    def test_metaproc_namespace(self) -> None:
        result = parse_schema_token("metaproc:UsageEnvelope/0.1")
        assert result == SchemaToken("metaproc", "UsageEnvelope", "0.1")

    def test_non_numeric_version_suffix(self) -> None:
        result = parse_schema_token("metaproc:QaReport/0.2beta")
        assert result.version == "0.2beta"

    def test_missing_colon(self) -> None:
        with pytest.raises(ValueError, match="Malformed"):
            parse_schema_token("metaprocUsageEnvelope/0.1")

    def test_missing_slash(self) -> None:
        with pytest.raises(ValueError, match="Malformed"):
            parse_schema_token("metaproc:UsageEnvelope")

    def test_invalid_module_starts_with_digit(self) -> None:
        with pytest.raises(ValueError, match="Invalid module"):
            parse_schema_token("3metaproc:UsageEnvelope/0.1")

    def test_invalid_class_not_pascal(self) -> None:
        with pytest.raises(ValueError, match="Invalid class name"):
            parse_schema_token("metaproc:usage_envelope/0.1")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Malformed"):
            parse_schema_token("")


class TestFormatSchemaToken:
    def test_basic(self) -> None:
        assert (
            format_schema_token("metaproc", "UsageEnvelope", "0.1") == "metaproc:UsageEnvelope/0.1"
        )

    def test_invalid_module(self) -> None:
        with pytest.raises(ValueError, match="Invalid module"):
            format_schema_token("bad-module", "UsageEnvelope", "0.1")

    def test_invalid_class(self) -> None:
        with pytest.raises(ValueError, match="Invalid class name"):
            format_schema_token("metaproc", "not_pascal", "0.1")

    def test_empty_version(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            format_schema_token("metaproc", "UsageEnvelope", "")


class TestRoundTrip:
    @pytest.mark.parametrize(
        "token",
        [
            "metaproc:UsageEnvelope/0.1",
            "example_workflow:RecordDocument/0.1",
            "metaproc:QaReport/1.0rc1",
        ],
    )
    def test_format_parse_round_trip(self, token: str) -> None:
        parsed = parse_schema_token(token)
        assert format_schema_token(parsed.module, parsed.class_name, parsed.version) == token


# ── Envelope schema enforcement ──────────────────────────────────


class TestEnvelopeSchemaEnforcement:
    """Every inner model reachable via ENVELOPE_MAP must have a valid schema_ default."""

    @staticmethod
    def _get_inner_models_with_tokens() -> list[tuple[str, str, type]]:
        """Extract (envelope_key, token, inner_class) for all ENVELOPE_MAP entries."""

        results: list[tuple[str, str, type]] = []
        for key, envelope_cls in get_registered_envelopes().items():
            for field_info in envelope_cls.model_fields.values():
                inner_type = field_info.annotation
                if inner_type is None:
                    continue
                if hasattr(inner_type, "model_fields") and "schema_" in inner_type.model_fields:
                    token = inner_type.model_fields["schema_"].default
                    if isinstance(token, str):
                        results.append((key, token, inner_type))
        return results

    # Envelope keys owned by metaproc core (not plugin-registered).
    # Plugin envelopes (example_workflow) will gain schema_ in Phase 2.
    _METAPROC_ENVELOPE_KEYS = {"plan", "process", "progress", "qa", "qa_summary", "usage"}

    def test_all_metaproc_envelope_inner_models_have_schema(self) -> None:
        """Every metaproc-owned envelope must have an inner model with a schema_ field."""

        models_with_schema = self._get_inner_models_with_tokens()
        envelope_keys_covered = {r[0] for r in models_with_schema}
        metaproc_keys = set(get_registered_envelopes()) & self._METAPROC_ENVELOPE_KEYS
        missing = metaproc_keys - envelope_keys_covered
        assert not missing, f"Envelope keys without schema_ on inner model: {missing}"

    @pytest.mark.parametrize(
        ("key", "token", "inner_cls"),
        _get_inner_models_with_tokens.__func__(),  # pyright: ignore[reportFunctionMemberAccess]
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_schema_token_is_valid(self, key: str, token: str, inner_cls: type) -> None:
        """Each inner model's default schema_ value must be a parseable token."""
        parsed = parse_schema_token(token)
        assert parsed.module == "metaproc"
        assert parsed.version  # non-empty


# ── resolve_schema ──────────────────────────────────────────────


class TestResolveSchema:
    def test_resolve_known_tokens(self) -> None:
        registry = _build_schema_registry()
        assert len(registry) >= 6  # all 6 metaproc models

    def test_resolve_usage_report(self) -> None:

        cls = resolve_schema("metaproc:UsageReport/0.2")
        assert cls is UsageReport

    def test_resolve_plan(self) -> None:

        # Current default token.
        cls = resolve_schema("metaproc:Plan/0.5")
        assert cls is Plan
        # Historical token still resolves to Plan via
        # historical_schema_tokens; documented in Plan's docstring.
        cls = resolve_schema("metaproc:Plan/0.4")
        assert cls is Plan

    def test_resolve_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown schema token"):
            resolve_schema("metaproc:NonExistent/9.9")
