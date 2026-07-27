"""Tests for metaproc.engine.retry — error classification and backoff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaproc.commands.run_parallel import _resolve_retry_policy
from metaproc.engine.retry import (
    MAX_CONTENT_FAILURE_RETRIES_DEFAULT,
    FailureClass,
    RetryVerdict,
    classify_error,
    classify_failure,
    compute_backoff,
    extract_log_error,
    max_retries_for,
)
from metaproc.models.authored import ForEach, ProcessDefaults, ProcessStep, RetryPolicy

# ── classify_error ─────────────────────────────────────────────


class TestClassifyErrorPermanent:
    """Permanent errors that should NOT be retried."""

    @pytest.mark.parametrize(
        "error",
        [
            "ENOSPC: no space left on device, write",
            "Error: ENOSPC: no space left on device",
            "ENOMEM: cannot allocate memory",
            "You exceeded your current quota",
            "API quota exceeded",
            "permission denied: /var/run/foo",
            "Your credit balance is too low",
            "Insufficient credits to continue",
            "billing account is disabled",
        ],
    )
    def test_permanent_patterns(self, error: str) -> None:
        assert classify_error(error) == RetryVerdict.FAIL

    def test_output_validation_failed_is_retryable(self) -> None:
        """Output validation failures are retryable — usually caused by
        transient issues (e.g. gcloud auth expiry mid-execution)."""
        assert (
            classify_error("output validation failed: record.md: file not found")
            == RetryVerdict.RETRY
        )

    def test_output_validation_failed_multiple_is_retryable(self) -> None:
        assert (
            classify_error(
                "output validation failed: record.md: file not found; summary.md: file not found"
            )
            == RetryVerdict.RETRY
        )

    def test_output_validation_with_log_error_gcloud(self) -> None:
        """When log extraction finds gcloud auth, the enriched error
        matches the transient pattern (checked before output validation)."""
        error = (
            "output validation failed: record.md: file not found "
            '(log: Failed to resolve API key for provider "vertex-maas" '
            "from shell command: gcloud auth print-access-token)"
        )
        assert classify_error(error) == RetryVerdict.RETRY


class TestClassifyErrorTransient:
    """Transient errors that SHOULD be retried."""

    @pytest.mark.parametrize(
        "error",
        [
            "timeout after 300s",
            "rate limit exceeded",
            "Too Many Requests",
            "429: rate limit",
            "ERROR_TRUNCATED_HEADERS",
            'Failed to resolve API key for provider "vertex-maas" from shell command: gcloud auth print-access-token',
            "UNAVAILABLE: service temporarily unavailable",
            "503 Service Unavailable",
            "502 Bad Gateway",
            "ECONNREFUSED 127.0.0.1:443",
            "ECONNRESET: connection reset by peer",
            "connection refused",
            "log_runaway: streaming anomaly detected — log is 500 MB for 100 output tokens (5000 KB/token, expected ~2 KB/token)",
            "stalled (no log output for 300s)",
            # P3.1.3 I2/I5 regression — exact 2026-04-22 Anthropic stream-idle
            # signature from CACI/CASH/CATY/CCI/EGBN/OSBC/SIGI/TNL/WCN/BANC and
            # the 2026-04-23 deadline-run transient cohort. Must classify as
            # RETRY so the framework retry-with-backoff default recovers the
            # ticker in-session across ~20 min without operator intervention.
            "API Error: Stream idle timeout - partial response received",
        ],
    )
    def test_transient_patterns(self, error: str) -> None:
        assert classify_error(error) == RetryVerdict.RETRY

    def test_bare_exit_code(self) -> None:
        """Bare exit codes with no recognized pattern default to RETRY."""
        assert classify_error("exit code 1") == RetryVerdict.RETRY

    def test_exit_code_137_is_permanent(self) -> None:
        """Exit code 137 (SIGKILL/OOM) is not retryable."""
        assert classify_error("exit code 137") == RetryVerdict.FAIL

    def test_exit_code_143_is_permanent(self) -> None:
        """Exit code 143 (user cancellation) is not retryable."""
        assert classify_error("exit code 143") == RetryVerdict.FAIL

    def test_cancelled_is_permanent(self) -> None:
        """Errors containing 'cancelled' are not retryable."""
        assert classify_error("job cancelled by user") == RetryVerdict.FAIL

    def test_command_exit_code(self) -> None:
        assert classify_error("command exit code 1") == RetryVerdict.RETRY


class TestClassifyErrorEdgeCases:
    """Edge cases and priority ordering."""

    def test_permanent_takes_priority_over_transient(self) -> None:
        """If both permanent and transient patterns match, permanent wins."""
        # "quota" is permanent, "timeout" is transient
        assert classify_error("quota timeout exceeded") == RetryVerdict.FAIL

    def test_unknown_error_is_fail(self) -> None:
        """Completely unrecognized errors default to FAIL."""
        assert classify_error("something completely unexpected happened") == RetryVerdict.FAIL

    def test_empty_string(self) -> None:
        assert classify_error("") == RetryVerdict.FAIL

    def test_case_insensitive(self) -> None:
        assert classify_error("ENOSPC") == RetryVerdict.FAIL
        assert classify_error("enospc") == RetryVerdict.FAIL
        assert classify_error("Rate Limit Exceeded") == RetryVerdict.RETRY

    def test_anthropic_monthly_usage_limit_is_permanent(self) -> None:
        """Anthropic personal-plan monthly cap on claude-code-cli.

        The claude-code-cli adapter emits this string in the JSONL `result`
        field on a 429 response with `api_error_status=429`. Distinct from a
        burst rate-limit (which is transient): a monthly cap will not lift on
        retry timescales and burning the retry budget on it accomplishes
        nothing.
        """
        assert classify_error("You've hit your org's monthly usage limit") == RetryVerdict.FAIL
        assert classify_error("api error 429: monthly limit exceeded") == RetryVerdict.FAIL

    def test_monthly_usage_limit_takes_priority_over_429(self) -> None:
        """When both 429 (transient) and `monthly usage limit` (permanent)
        appear in the same error, permanent wins."""
        err = "api_error_status=429 You've hit your org's monthly usage limit"
        assert classify_error(err) == RetryVerdict.FAIL


# ── classify_failure ──────────────────────────────────────────


class TestClassifyFailure:
    """Tests for failure class categorization."""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            ("429: rate limit", FailureClass.RATE_LIMITED),
            ("rate limit exceeded", FailureClass.RATE_LIMITED),
            ("Too Many Requests", FailureClass.RATE_LIMITED),
            ("timeout after 300s", FailureClass.TIMEOUT),
            ("stalled (no log output for 300s)", FailureClass.TIMEOUT),
            ("log_runaway: streaming anomaly", FailureClass.TIMEOUT),
            ("503 Service Unavailable", FailureClass.SERVER_ERROR),
            ("502 Bad Gateway", FailureClass.SERVER_ERROR),
            ("ECONNREFUSED 127.0.0.1:443", FailureClass.SERVER_ERROR),
            ("ECONNRESET: connection reset", FailureClass.SERVER_ERROR),
            ("connection refused", FailureClass.SERVER_ERROR),
            ("UNAVAILABLE: service temporarily unavailable", FailureClass.SERVER_ERROR),
            ("output validation failed: record.md: file not found", FailureClass.INVALID_OUTPUT),
            ("exit code 1", FailureClass.CRASH),
            ("command exit code 1", FailureClass.CRASH),
            ("something unexpected", FailureClass.UNKNOWN),
            # the fix: monthly-cap exhaustion is QUOTA_EXHAUSTED (distinct
            # from RATE_LIMITED so the operator surface can distinguish the two
            # — wait until reset vs. wait a few seconds and retry).
            (
                "You've hit your org's monthly usage limit",
                FailureClass.QUOTA_EXHAUSTED,
            ),
            ("monthly limit reached", FailureClass.QUOTA_EXHAUSTED),
            ("anthropic quota exceeded", FailureClass.QUOTA_EXHAUSTED),
            # The 2026-05-13 cascade message — verbatim text from Claude
            # Code result events on a quota-exhausted account. classify_failure
            # has to recognize this so run_parallel's quota-retry path can fire.
            (
                "You're out of extra usage · resets 3:10am (America/Los_Angeles)",
                FailureClass.QUOTA_EXHAUSTED,
            ),
            ("out of usage · resets 5pm", FailureClass.QUOTA_EXHAUSTED),
        ],
    )
    def test_failure_classification(self, error: str, expected: FailureClass) -> None:
        assert classify_failure(error) == expected

    def test_case_insensitive(self) -> None:
        assert classify_failure("RATE LIMIT EXCEEDED") == FailureClass.RATE_LIMITED
        assert classify_failure("Rate Limit") == FailureClass.RATE_LIMITED

    def test_output_validation_with_429_log_is_rate_limited(self) -> None:
        error = "output validation failed: record.md: file not found (log: 429 status code)"
        assert classify_failure(error) == FailureClass.RATE_LIMITED

    def test_anthropic_internal_server_error_is_server_error(self) -> None:
        """The exact error string the run_parallel orchestrator sees when
        an Anthropic 500 lands inside a claude-code subprocess. Was being
        classified as CRASH because no 500/api_error pattern matched.
        A production run encountered this in a setup step."""
        err = (
            'exit code 1 (log: API Error: {"type":"error",'
            '"error":{"details":null,"type":"api_error",'
            '"message":"Internal server error"},'
            '"request_id":"req_011Cb1DgFVcAAj3w7n3tee1Q"})'
        )
        assert classify_failure(err) == FailureClass.SERVER_ERROR
        assert classify_error(err) == RetryVerdict.RETRY

    def test_quota_exhausted_takes_priority_over_rate_limited(self) -> None:
        """Both '429' (rate-limited) and 'monthly usage limit' (quota) match;
        the more specific QUOTA_EXHAUSTED wins so operators get the right signal."""
        err = "api_error_status=429 You've hit your org's monthly usage limit"
        assert classify_failure(err) == FailureClass.QUOTA_EXHAUSTED


# ── compute_backoff ────────────────────────────────────────────


class TestComputeBackoff:
    def test_first_attempt(self) -> None:
        policy = RetryPolicy(
            max_retries=3, initial_backoff_s=5.0, backoff_multiplier=2.0, max_backoff_s=120.0
        )
        assert compute_backoff(1, policy) == 5.0

    def test_exponential_growth(self) -> None:
        policy = RetryPolicy(
            max_retries=5, initial_backoff_s=2.0, backoff_multiplier=2.0, max_backoff_s=120.0
        )
        assert compute_backoff(1, policy) == 2.0
        assert compute_backoff(2, policy) == 4.0
        assert compute_backoff(3, policy) == 8.0
        assert compute_backoff(4, policy) == 16.0

    def test_capped_at_max(self) -> None:
        policy = RetryPolicy(
            max_retries=10, initial_backoff_s=5.0, backoff_multiplier=3.0, max_backoff_s=60.0
        )
        # 5 * 3^3 = 135, but capped at 60
        assert compute_backoff(4, policy) == 60.0

    def test_multiplier_one(self) -> None:
        """With multiplier=1, backoff is constant."""
        policy = RetryPolicy(
            max_retries=3, initial_backoff_s=10.0, backoff_multiplier=1.0, max_backoff_s=120.0
        )
        assert compute_backoff(1, policy) == 10.0
        assert compute_backoff(3, policy) == 10.0


# ── RetryPolicy model ─────────────────────────────────────────


class TestRetryPolicy:
    def test_defaults(self) -> None:
        """Framework default is retry-with-backoff ON across all fan-out steps.

        Transient-error retry is treated as a framework baseline rather than a
        per-step opt-in: every fan-out step gets 12 retries with 1.5× backoff
        (5s, 7.5s, 11.25s, ... capped at 600s; ~21.5 min total) unless it opts
        out via --no-retry or overrides with an explicit retry: block. The
        span is sized for upstream infra blips that can take several minutes
        to recover, not just second-scale 429 bursts.
        """
        policy = RetryPolicy()
        assert policy.max_retries == 12
        assert policy.initial_backoff_s == 5.0
        assert policy.backoff_multiplier == 1.5
        assert policy.max_backoff_s == 600.0

    def test_custom_values(self) -> None:
        policy = RetryPolicy(max_retries=5, initial_backoff_s=1.0, max_backoff_s=30.0)
        assert policy.max_retries == 5
        assert policy.initial_backoff_s == 1.0
        assert policy.max_backoff_s == 30.0

    def test_from_dict(self) -> None:
        """RetryPolicy can be constructed from a dict (as from YAML parsing)."""
        data = {"max_retries": 3, "initial_backoff_s": 10}
        policy = RetryPolicy(**data)
        assert policy.max_retries == 3
        assert policy.initial_backoff_s == 10.0


# ── _resolve_retry_policy ────────────────────────────────────


def _make_for_each(
    retry: RetryPolicy | None = None,
) -> ForEach:
    """Helper to build a current ForEach."""
    return ForEach.model_validate(
        {
            "over": "items",
            "bind": "ticker",
            "bind_fields": ["ticker"],
            **({"retry": retry.model_dump()} if retry else {}),
        }
    )


def _make_step(for_each: ForEach | None = None) -> ProcessStep:
    """Helper to build a minimal ProcessStep."""
    return ProcessStep(id="s", mode="agent", for_each=for_each)


class TestResolveRetryPolicy:
    """Tests for CLI/spec retry policy resolution."""

    def setup_method(self) -> None:

        self._resolve = _resolve_retry_policy  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_default_applies_framework_retry_profile(self) -> None:
        """With no spec config and no CLI flags, the framework retry profile
        applies (retry-with-backoff is the default, not off)."""
        policy = self._resolve(_make_step(), ProcessDefaults())
        assert policy.max_retries == 12
        assert policy.initial_backoff_s == 5.0
        assert policy.backoff_multiplier == 1.5
        assert policy.max_backoff_s == 600.0

    def test_no_retry_flag_overrides_spec(self) -> None:
        """--no-retry disables retries even when spec defines a policy."""
        step_def = _make_step(for_each=_make_for_each(retry=RetryPolicy(max_retries=5)))
        policy = self._resolve(step_def, ProcessDefaults(), no_retry=True)
        assert policy.max_retries == 0

    def test_step_level_retry(self) -> None:
        """Step-level for_each.retry takes precedence over defaults."""
        step_def = _make_step(
            for_each=_make_for_each(retry=RetryPolicy(max_retries=3, initial_backoff_s=10)),
        )
        defaults = ProcessDefaults(retry=RetryPolicy(max_retries=1))
        policy = self._resolve(step_def, defaults)
        assert policy.max_retries == 3
        assert policy.initial_backoff_s == 10.0

    def test_defaults_level_retry(self) -> None:
        """Process-level defaults.retry is used when step has no override."""
        step_def = _make_step(for_each=_make_for_each())
        defaults = ProcessDefaults(retry=RetryPolicy(max_retries=2, max_backoff_s=60))
        policy = self._resolve(step_def, defaults)
        assert policy.max_retries == 2
        assert policy.max_backoff_s == 60.0

    def test_cli_max_retries_override(self) -> None:
        """--max-retries CLI flag overrides spec max_retries."""
        step_def = _make_step(
            for_each=_make_for_each(retry=RetryPolicy(max_retries=1)),
        )
        policy = self._resolve(step_def, ProcessDefaults(), max_retries_override=5)
        assert policy.max_retries == 5
        # Other fields from the step's RetryPolicy() are preserved — those
        # default to the framework profile when not explicitly set.
        assert policy.initial_backoff_s == 5.0
        assert policy.backoff_multiplier == 1.5
        assert policy.max_backoff_s == 600.0

    def test_cli_max_retries_with_no_spec(self) -> None:
        """--max-retries works even without spec-level retry config."""
        policy = self._resolve(_make_step(), ProcessDefaults(), max_retries_override=3)
        assert policy.max_retries == 3

    def test_no_for_each_key(self) -> None:
        """Step with no for_each falls back to defaults."""
        step_def = _make_step()
        defaults = ProcessDefaults(retry=RetryPolicy(max_retries=2))
        policy = self._resolve(step_def, defaults)
        assert policy.max_retries == 2


# ── extract_log_error ─────────────────────────────────────────


class TestExtractLogError:
    """Tests for extracting the terminal error from subprocess logs."""

    def test_agent_end_with_error_message(self, tmp_path: Path) -> None:
        """Extracts errorMessage from agent_end event."""

        log = tmp_path / "test.jsonl"
        events = [
            {"type": "message_end", "message": {"role": "assistant", "content": []}},
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "errorMessage": 'Failed to resolve API key for provider "vertex-maas"',
                    }
                ],
            },
        ]
        log.write_text("\n".join(json.dumps(e) for e in events))
        result = extract_log_error(log)
        assert result is not None
        assert "Failed to resolve API key" in result

    def test_non_json_stderr_line(self, tmp_path: Path) -> None:
        """Extracts raw stderr lines (non-JSON) as error signal."""
        log = tmp_path / "test.jsonl"
        log.write_text('{"type":"message_end"}\nERROR: gcloud auth failed\n')
        result = extract_log_error(log)
        assert result is not None
        assert "gcloud auth" in result

    def test_no_error_returns_none(self, tmp_path: Path) -> None:
        """Returns None when log has no error signal."""

        log = tmp_path / "test.jsonl"
        events = [
            {"type": "message_end", "message": {"role": "assistant", "content": []}},
            {"type": "agent_end", "messages": [{"role": "assistant", "content": []}]},
        ]
        log.write_text("\n".join(json.dumps(e) for e in events))
        assert extract_log_error(log) is None

    def test_missing_log_returns_none(self, tmp_path: Path) -> None:
        """Returns None when log file doesn't exist."""
        assert extract_log_error(tmp_path / "nonexistent.jsonl") is None

    def test_truncates_long_error(self, tmp_path: Path) -> None:
        """Error messages are truncated to 200 chars."""
        log = tmp_path / "test.jsonl"
        log.write_text("x" * 300 + "\n")
        result = extract_log_error(log)
        assert result is not None
        assert len(result) == 200


# ── max_retries_for ────────────────────────────────────────────


class TestMaxRetriesFor:
    """Per-failure-class retry budget cap."""

    def test_invalid_output_capped_at_default(self) -> None:
        """INVALID_OUTPUT failures cap at MAX_CONTENT_FAILURE_RETRIES_DEFAULT."""
        # Even when the operator-set max_retries is generous (12), content
        # failures cap at the smaller MAX_CONTENT_FAILURE_RETRIES_DEFAULT.
        assert (
            max_retries_for(FailureClass.INVALID_OUTPUT, 12) == MAX_CONTENT_FAILURE_RETRIES_DEFAULT
        )

    def test_invalid_output_floor_when_max_is_smaller(self) -> None:
        """If the operator-set max is BELOW the content cap, the smaller wins."""
        # A user explicitly setting --max-retries 1 should still get 1, not 3.
        assert max_retries_for(FailureClass.INVALID_OUTPUT, 1) == 1

    def test_transient_classes_use_default_max(self) -> None:
        """Non-content failures use the full operator-set max_retries budget."""
        for fc in (
            FailureClass.RATE_LIMITED,
            FailureClass.SERVER_ERROR,
            FailureClass.TIMEOUT,
            FailureClass.CRASH,
            FailureClass.UNKNOWN,
        ):
            assert max_retries_for(fc, 12) == 12, f"{fc} should use default budget"

    def test_quota_exhausted_uses_default(self) -> None:
        """QUOTA_EXHAUSTED uses the operator's full budget (waits for reset)."""
        assert max_retries_for(FailureClass.QUOTA_EXHAUSTED, 12) == 12
