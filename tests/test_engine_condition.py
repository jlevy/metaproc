"""Tests for metaproc.engine.condition.output_is_active."""

from __future__ import annotations

import pytest

from metaproc.engine.condition import output_is_active


class TestOutputIsActive:
    def test_none_condition_is_active(self):
        assert output_is_active(None) is True
        assert output_is_active(None, {"RUN_MODE": "live"}) is True

    def test_empty_condition_is_active(self):
        assert output_is_active("") is True

    def test_unquoted_equality_condition_matches(self):
        assert (
            output_is_active("WORKFLOW_MODE == historical", {"WORKFLOW_MODE": "historical"}) is True
        )

    def test_equality_condition_rejects_different_value(self):
        assert output_is_active("WORKFLOW_MODE == historical", {"WORKFLOW_MODE": "live"}) is False

    def test_quoted_equality_condition_is_normalized(self):
        assert (
            output_is_active("WORKFLOW_MODE == 'historical'", {"WORKFLOW_MODE": "historical"})
            is True
        )
        assert (
            output_is_active('WORKFLOW_MODE == "historical"', {"WORKFLOW_MODE": "historical"})
            is True
        )

    def test_no_variables_with_condition_is_inactive(self):
        assert output_is_active("WORKFLOW_MODE == historical", None) is False

    def test_missing_variable_is_inactive(self):
        assert output_is_active("WORKFLOW_MODE == historical", {}) is False

    def test_unsupported_condition_raises(self):
        with pytest.raises(NotImplementedError):
            output_is_active("WORKFLOW_MODE != historical", {"WORKFLOW_MODE": "historical"})
