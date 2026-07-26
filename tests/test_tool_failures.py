"""Tests for metaproc.logutil.tool_failures — tool-call failure classification.

The classifier takes a single tool_result block from an agent event stream and
assigns one of:

    ok, malformed_args, tool_timeout, tool_error, help_invocation,
    rate_limit_exhausted, adapter_dropped_call, unknown

``adapter_dropped_call`` is cross-event (no matching tool_result followed a
tool_use) and must be assigned by the caller; the classifier never emits it.
"""

from __future__ import annotations

import pytest

from metaproc.logutil.tool_failures import (
    TOOL_FAILURE_KINDS,
    FailureKind,
    classify_pi_tool_result,
)


class TestFailureKindEnum:
    def test_enum_values_match_contract(self) -> None:
        expected = {
            "ok",
            "malformed_args",
            "tool_timeout",
            "tool_error",
            "help_invocation",
            "tool_rejected",
            "rate_limit_exhausted",
            "adapter_dropped_call",
            "unknown",
        }
        assert {k.value for k in FailureKind} == expected

    def test_kinds_iterable_is_stable(self) -> None:
        # TOOL_FAILURE_KINDS preserves declaration order for deterministic aggregation.
        assert TOOL_FAILURE_KINDS[0] == FailureKind.OK
        assert TOOL_FAILURE_KINDS[-1] == FailureKind.UNKNOWN


class TestClassifyPiToolResult:
    def test_ok_when_is_error_false(self) -> None:
        block = {"type": "tool_result", "is_error": False, "content": "result text"}
        assert classify_pi_tool_result(block) == FailureKind.OK

    def test_rate_limit_by_content_keyword_even_with_is_error_true(self) -> None:
        block = {
            "type": "tool_result",
            "is_error": True,
            "content": "Provider returned HTTP 429 rate_limit_exceeded",
        }
        assert classify_pi_tool_result(block) == FailureKind.RATE_LIMIT_EXHAUSTED

    def test_timeout_by_content_keyword(self) -> None:
        block = {"type": "tool_result", "is_error": True, "content": "Error: request timed out"}
        assert classify_pi_tool_result(block) == FailureKind.TOOL_TIMEOUT

    def test_generic_is_error_true_is_tool_error(self) -> None:
        block = {"type": "tool_result", "is_error": True, "content": "generic failure"}
        assert classify_pi_tool_result(block) == FailureKind.TOOL_ERROR

    def test_fails_hard_on_non_tool_result(self) -> None:
        with pytest.raises(ValueError, match="tool_result"):
            classify_pi_tool_result({"type": "text", "is_error": False})
