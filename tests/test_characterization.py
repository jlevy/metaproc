"""Characterization tests for Phase 4b-4c — run_step/run_parallel and build_plan.

These tests pin CURRENT behavior of resolve_runtime_config, validate_item_outputs,
classify_error, compute_backoff, build_plan, and merge_defaults to serve as a
safety net for future decomposition.

Each test documents one specific behavior so that regressions during refactoring
are immediately visible.
"""

# pyright: reportIndexIssue=false

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from metaproc.engine.build_plan import build_plan, merge_defaults
from metaproc.engine.placeholders import resolve_runtime_config
from metaproc.engine.retry import RetryVerdict, classify_error, compute_backoff
from metaproc.engine.validation import validate_item_outputs
from metaproc.models.authored import (
    AdapterConfig,
    ForEach,
    IOSpec,
    ProcessDefaults,
    ProcessSpec,
    ProcessStep,
    RetryPolicy,
)

# ── 4b: resolve_runtime_config ───────────────────────────────────


class TestResolveRuntimeConfig:
    """Pin template resolution within adapter config structures."""

    def test_replaces_string_templates(self) -> None:
        """String values with {{VAR}} are replaced from the variables dict."""
        config = {"model": "{{MODEL}}", "timeout_s": 300, "api_key": "{{API_KEY}}"}
        variables = {"MODEL": "claude-3", "API_KEY": "sk-123"}
        result = resolve_runtime_config(config, variables)
        assert result["model"] == "claude-3"
        assert result["api_key"] == "sk-123"
        assert result["timeout_s"] == 300

    def test_unresolved_templates_pass_through(self) -> None:
        """Templates without matching variables are left as-is (double-brace syntax)."""
        config = {"model": "{{MISSING}}"}
        result = resolve_runtime_config(config, {})
        assert result["model"] == "{{MISSING}}"

    def test_nested_dict_resolution(self) -> None:
        """Nested dicts are recursively resolved."""
        config = {"outer": {"inner": "{{VAL}}"}}
        result = resolve_runtime_config(config, {"VAL": "resolved"})
        assert result["outer"]["inner"] == "resolved"

    def test_list_resolution(self) -> None:
        """List elements are recursively resolved."""
        config = {"items": ["{{A}}", "literal", "{{B}}"]}
        result = resolve_runtime_config(config, {"A": "1", "B": "2"})
        assert result["items"] == ["1", "literal", "2"]


# ── 4b: validate_item_outputs ────────────────────────────────────


class TestValidateItemOutputs:
    """Pin output validation behavior: file existence checks."""

    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        """A declared output whose file does not exist produces an error."""
        outputs = {
            "record": IOSpec(path="record.md"),
        }
        errors = validate_item_outputs(tmp_path, outputs)
        assert len(errors) == 1
        assert "record.md" in errors[0]
        assert "not found" in errors[0]

    def test_existing_file_passes(self, tmp_path: Path) -> None:
        """A declared output whose file exists produces no error."""
        (tmp_path / "record.md").write_text("content")
        outputs = {
            "record": IOSpec(path="record.md"),
        }
        errors = validate_item_outputs(tmp_path, outputs)
        assert errors == []

    def test_optional_field_empty_path_skipped(self, tmp_path: Path) -> None:
        """An output spec with an empty path string is silently skipped."""
        outputs = {
            "empty": IOSpec(path=""),
        }
        errors = validate_item_outputs(tmp_path, outputs)
        assert errors == []

    def test_empty_directory_output_is_error(self, tmp_path: Path) -> None:
        """A directory output that exists but is empty is a silent-success failure."""
        (tmp_path / "records").mkdir()
        outputs = {
            "records": IOSpec(path="records", kind="directory"),
        }
        errors = validate_item_outputs(tmp_path, outputs)
        assert len(errors) == 1
        assert "empty" in errors[0]


# ── 4b: retry classification ─────────────────────────────────────


class TestClassifyError:
    """Pin error classification: permanent vs transient patterns."""

    def test_timeout_is_retryable(self) -> None:
        """Timeout errors are classified as transient (retryable)."""
        assert classify_error("request timeout after 300s") == RetryVerdict.RETRY

    def test_rate_limit_is_retryable(self) -> None:
        """Rate-limit errors are classified as transient."""
        assert classify_error("429 too many requests") == RetryVerdict.RETRY

    def test_permission_denied_is_permanent(self) -> None:
        """Permission denied is a permanent failure."""
        assert classify_error("permission denied: /var/run/secret") == RetryVerdict.FAIL

    def test_bare_exit_code_is_retryable(self) -> None:
        """A bare 'exit code N' with no other signal is retryable."""
        assert classify_error("exit code 1") == RetryVerdict.RETRY

    def test_unknown_error_is_permanent(self) -> None:
        """Unrecognized errors default to FAIL."""
        assert classify_error("something completely unknown") == RetryVerdict.FAIL

    def test_output_validation_missing_file_is_retryable(self) -> None:
        """Output validation failure for missing files is retryable."""
        assert classify_error("output validation failed: record.md not found") == RetryVerdict.RETRY

    def test_output_validation_schema_mismatch_is_permanent(self) -> None:
        """Output validation failure for schema mismatch is permanent."""
        assert (
            classify_error("output validation failed: schema mismatch in record.md")
            == RetryVerdict.FAIL
        )

    def test_output_validation_envelope_mismatch_is_permanent(self) -> None:
        """Output validation failure for envelope mismatch is permanent."""
        assert classify_error("output validation failed: envelope key missing") == RetryVerdict.FAIL


class TestComputeBackoff:
    """Pin exponential backoff calculation."""

    def test_first_attempt_uses_initial_backoff(self) -> None:
        """Attempt 1 returns the initial backoff value."""
        policy = RetryPolicy(initial_backoff_s=5.0, backoff_multiplier=2.0, max_backoff_s=120.0)
        assert compute_backoff(1, policy) == 5.0

    def test_exponential_growth(self) -> None:
        """Each subsequent attempt doubles the delay (multiplier=2)."""
        policy = RetryPolicy(initial_backoff_s=5.0, backoff_multiplier=2.0, max_backoff_s=120.0)
        assert compute_backoff(2, policy) == 10.0
        assert compute_backoff(3, policy) == 20.0

    def test_capped_at_max(self) -> None:
        """Backoff is capped at max_backoff_s."""
        policy = RetryPolicy(initial_backoff_s=5.0, backoff_multiplier=2.0, max_backoff_s=30.0)
        assert compute_backoff(10, policy) == 30.0


# ── 4c: merge_defaults ───────────────────────────────────────────


class TestMergeDefaults:
    """Pin config merging: process defaults + step overrides."""

    def test_step_without_adapter_inherits_defaults(self) -> None:
        """A step with no adapter override inherits the process default adapter."""

        defaults = ProcessDefaults(
            adapters={
                "default": AdapterConfig(
                    type="claude-code-cli",
                    config={"timeout_s": 600, "model": "opus"},
                )
            },
            default_adapter="default",
        )
        step = ProcessStep(
            id="s1",
            mode="agent",
            prompt_prefix="do stuff",
            outputs={"main": IOSpec(path="out.md")},
        )
        merged = merge_defaults(step, defaults)
        assert merged["adapter_type"] == "claude-code-cli"
        assert merged["timeout"] == 600
        assert merged["model"] == "opus"

    def test_step_adapter_override_merges(self) -> None:
        """A step with an adapter override merges its config on top of defaults."""

        defaults = ProcessDefaults(
            adapters={
                "default": AdapterConfig(
                    type="claude-code-cli",
                    config={"timeout_s": 600, "model": "opus"},
                )
            },
            default_adapter="default",
        )
        step = ProcessStep(
            id="s1",
            mode="agent",
            prompt_prefix="do stuff",
            outputs={"main": IOSpec(path="out.md")},
            adapter=AdapterConfig(type="pi-cli", config={"model": "deepseek"}),
        )
        merged = merge_defaults(step, defaults)
        assert merged["adapter_type"] == "pi-cli"
        # Step override replaces the model
        assert merged["model"] == "deepseek"


# ── 4c: build_plan ───────────────────────────────────────────────


def _minimal_spec(**overrides: Any) -> ProcessSpec:
    """Create a minimal ProcessSpec for testing build_plan."""
    defaults = overrides.pop(
        "defaults",
        ProcessDefaults(
            adapters={
                "default": AdapterConfig(type="claude-code-cli", config={"timeout_s": 300}),
            },
            default_adapter="default",
        ),
    )
    steps = overrides.pop(
        "steps",
        [
            ProcessStep(
                id="step1",
                mode="agent",
                prompt_prefix="hello {{TICKER}}",
                outputs={"main": IOSpec(path="out.md")},
            )
        ],
    )
    return ProcessSpec(
        name=overrides.pop("name", "test-process"),
        defaults=defaults,
        steps=steps,
        **overrides,
    )


class TestBuildPlan:
    """Pin build_plan: adapter resolution, config coercion, fan-out discovery."""

    @patch(
        "metaproc.engine.build_plan.ADAPTER_REGISTRY",
        {"claude-code-cli": MagicMock(), "pi-cli": MagicMock()},
    )
    def test_adapter_override_changes_resolved_type(self) -> None:
        """An adapter_override parameter overrides adapter type for all steps."""

        spec = _minimal_spec()
        plan = build_plan(
            spec,
            {"TICKER": "AAPL"},
            process_path=Path("/fake/process.md"),
            adapter_override="pi-cli",
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].adapter.type == "pi-cli"

    @patch("metaproc.engine.build_plan.ADAPTER_REGISTRY", {"claude-code-cli": MagicMock()})
    def test_config_overrides_coerced(self) -> None:
        """Config overrides are type-coerced (e.g. timeout_s becomes int)."""

        spec = _minimal_spec()
        plan = build_plan(
            spec,
            {"TICKER": "AAPL"},
            process_path=Path("/fake/process.md"),
            config_overrides={"timeout_s": "900", "verbose": "true"},
        )
        config = plan.steps[0].adapter.config
        assert config["timeout_s"] == 900
        assert config["verbose"] is True

    @patch("metaproc.engine.build_plan.ADAPTER_REGISTRY", {"claude-code-cli": MagicMock()})
    def test_prompt_templates_resolved(self) -> None:
        """Prompt placeholders are resolved from params."""

        spec = _minimal_spec()
        plan = build_plan(
            spec,
            {"TICKER": "MSFT"},
            process_path=Path("/fake/process.md"),
        )
        assert plan.steps[0].prompt_prefix == "hello MSFT"

    @patch("metaproc.engine.build_plan.ADAPTER_REGISTRY", {"claude-code-cli": MagicMock()})
    @patch("metaproc.engine.build_plan.discover_items_from_source")
    def test_fan_out_discovery(self, mock_discover: MagicMock) -> None:
        """Fan-out items are discovered from source path when it exists."""

        mock_discover.return_value = MagicMock(
            actionable_contexts=[{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            filtered_items=[],
        )

        spec = _minimal_spec(
            steps=[
                ProcessStep(
                    id="predict",
                    mode="agent",
                    prompt_prefix="analyze {{ticker}}",
                    inputs={"tickers": IOSpec(path="tickers.yaml")},
                    outputs={"main": IOSpec(path="records/{{ticker}}/record.md")},
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )

        with patch("metaproc.engine.build_plan.Path.exists", return_value=True):
            plan = build_plan(
                spec,
                {"TICKER": "AAPL"},
                process_path=Path("/fake/process.md"),
            )

        assert plan.steps[0].fan_out is not None
        assert len(plan.steps[0].fan_out.items) == 2
        assert plan.steps[0].fan_out.bind == "ticker"

    @patch("metaproc.engine.build_plan.ADAPTER_REGISTRY", {"claude-code-cli": MagicMock()})
    def test_missing_required_param_raises(self) -> None:
        """build_plan raises ValueError when a required param is missing."""

        spec = _minimal_spec(
            inputs={
                "ticker": {
                    "param": "TICKER",
                    "as": "string",
                }
            }
        )
        with pytest.raises(ValueError, match="missing required input: ticker"):
            build_plan(spec, {}, process_path=Path("/fake/process.md"))
