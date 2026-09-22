"""Credential handling and missing-evidence behavior for durable command errors."""

import json

import pytest

from metaproc.engine.command_diagnostics import (
    command_failure_message,
    failure_cause,
    handler_failure_message,
    summarize_failure_causes,
    without_evidence_paths,
)
from metaproc.engine.retry import FailureClass, RetryVerdict


def test_ansi_normalization_cannot_reconstruct_a_redacted_secret() -> None:
    secret = "opaque-private-value"
    error = command_failure_message(
        1,
        stdout=None,
        stderr="secret is opaque-\x1b[31mprivate\x1b[0m-value",
        env={"PROVIDER_API_KEY": secret},
        log_path="task.log",
    ).error
    assert secret not in error
    assert "[redacted]" in error
    assert "\x1b" not in error


def test_credential_forms_are_redacted_before_a_long_diagnostic_is_truncated() -> None:
    secret = "opaque-secret-" + "a" * 2_000
    stderr = (
        f"unlabeled credential: {secret}\n"
        'provider response: {"api_key": "response-secret"}\n'
        "request: https://user:password@example.invalid/query?token=query-secret&limit=5\n"
        "Authorization: Basic dXNlcjpwYXNzd29yZA==\n"
        "ProviderError: HTTP 403 denied\n"
    )
    error = command_failure_message(
        3, stdout=None, stderr=stderr, env={"SERVICE_AUTH": secret}, log_path="task.log"
    ).error
    assert "ProviderError: HTTP 403 denied" in error
    assert "command exit code 3" in error
    assert "log: task.log" in error
    for value in ("opaque-secret", "response-secret", "user:password", "query-secret", "dXNlcj"):
        assert value not in error
    assert "[redacted]" in error
    assert len(error) < 1_000


def test_empty_capture_does_not_invent_a_diagnostic_or_log_file() -> None:
    error = command_failure_message(
        11, stdout="", stderr="\n", env={}, log_path="not-written.log"
    ).error
    assert error == "command exit code 11 (no stdout/stderr captured)"


def test_auth_configuration_is_not_a_secret_but_declared_secret_targets_are() -> None:
    diagnostic = "ProviderError: HTTP 429; Retry-After: 17; credential: opaque-private-value"
    error = command_failure_message(
        1,
        stdout=None,
        stderr=diagnostic,
        env={
            "METAPROC_AUTH_POOL_LOCK_TIMEOUT_S": "17",
            "METAPROC_GCP_SECRET_REFS_JSON": '{"SERVICE_VALUE":"projects/example/secrets/service/versions/latest"}',
            "SERVICE_VALUE": "opaque-private-value",
        },
        log_path="task.log",
    ).error
    assert "Retry-After: 17" in error
    assert "opaque-private-value" not in error


def test_public_unknown_env_values_preserve_provider_status_and_retry_timing() -> None:
    error = command_failure_message(
        1,
        stdout=None,
        stderr="ProviderError: HTTP 429; Retry-After: 17; credentials are xy and opaque-api-value",
        env={
            "AUTH_TIMEOUT_S": "17",
            "PUBLIC_KEY_ID": "429",
            "TOKEN_COUNT": "17",
            "CUSTOM_API_KEY": "opaque-api-value",
            "METAPROC_GCP_SECRET_REFS_JSON": '{"CUSTOM_KEY_ID":"projects/example/secrets/id/versions/latest"}',
            "CUSTOM_KEY_ID": "xy",
        },
        log_path="task.log",
    ).error
    assert "HTTP 429; Retry-After: 17" in error
    assert "credentials are [redacted] and [redacted]" in error


def test_short_boolean_and_numeric_values_under_secret_like_names_are_not_redacted() -> None:
    """A name heuristic alone cannot make ``true`` or ``9`` a credential.

    Replacing such values would rewrite ordinary words and status codes, and
    classification deliberately reads the redacted text.
    """
    untrue = command_failure_message(
        1,
        stdout=None,
        stderr="ValueError: construed input is untrue",
        env={"NPM_CONFIG_ALWAYS_AUTH": "true"},
        log_path="task.log",
    )
    assert "construed input is untrue" in untrue.error
    assert "[redacted]" not in untrue.error

    handler = handler_failure_message(
        RuntimeError("HTTP 429 rate exceeded; Retry-After: 19"),
        env={"SKIP_AUTH": "9"},
        log_path="task.log",
    )
    assert "HTTP 429 rate exceeded; Retry-After: 19" in handler.error
    assert handler.verdict is RetryVerdict.RETRY
    assert handler.failure_class is FailureClass.RATE_LIMITED

    command = command_failure_message(
        1,
        stdout=None,
        stderr="ProviderError: HTTP 429; Retry-After: 19; request 12345678901234 disabled",
        env={"BUILD_KEY": "9", "SERVICE_TOKEN": "12345678901234", "LEGACY_AUTH": "disabled"},
        log_path="task.log",
    )
    assert "HTTP 429; Retry-After: 19; request 12345678901234 disabled" in command.error
    assert command.failure_class is FailureClass.RATE_LIMITED


def test_declared_and_framework_secrets_are_redacted_whatever_their_length() -> None:
    error = command_failure_message(
        1,
        stdout=None,
        stderr="refused values: q7 and z9 and 4242",
        env={
            "METAPROC_GCP_SECRET_REFS_JSON": '{"SERVICE_VALUE":"projects/example/secrets/value/versions/latest"}',
            "SERVICE_VALUE": "q7",
            "GH_TOKEN": "z9",
            "ANTHROPIC_API_KEY": "4242",
        },
        log_path="task.log",
    ).error
    assert "refused values: [redacted] and [redacted] and [redacted]" in error


def test_giant_line_keeps_the_terminal_diagnostic_and_marks_omission() -> None:
    diagnostic = "ProviderError: HTTP 503 unavailable; Retry-After: 25"
    error = command_failure_message(
        4, stdout="x" * 20_000 + diagnostic, stderr=None, env={}, log_path="task.log"
    ).error
    assert "stdout:" in error
    assert diagnostic in error
    assert "[truncated]" in error
    assert len(error) < 1_400


def test_classification_uses_redacted_diagnostics_before_display_truncation() -> None:
    failure = command_failure_message(
        1,
        stdout=None,
        stderr="HTTP 429 Too Many Requests\n" + "cleanup complete\n" * 13,
        env={},
        log_path="task.log",
    )
    assert failure.failure_class is FailureClass.RATE_LIMITED
    assert failure.verdict is RetryVerdict.RETRY
    assert "[truncated]" in failure.error
    assert "HTTP 429" not in failure.error

    secret_only = command_failure_message(
        1,
        stdout=None,
        stderr="ValueError: invalid input; opaque-429-value",
        env={"PROVIDER_API_KEY": "opaque-429-value"},
        log_path="quota-check/process_20260913T000503Z.log",
    )
    assert secret_only.failure_class is FailureClass.CRASH
    assert "opaque-429-value" not in secret_only.error


def test_handler_classification_excludes_redacted_secrets_and_traceback_filenames() -> None:
    failure = handler_failure_message(
        ValueError("invalid input; opaque-429-value"),
        env={"PROVIDER_API_KEY": "opaque-429-value"},
        log_path=".logs/tasks/quota-check/process_20260913T000503Z.log",
    )
    assert failure.failure_class is FailureClass.UNKNOWN
    assert failure.verdict is RetryVerdict.FAIL
    assert failure.error.startswith("ValueError: invalid input; [redacted]")
    assert "opaque-429-value" not in failure.error
    assert "command exit code" not in failure.error


@pytest.mark.parametrize("handler", [False, True])
def test_encoded_secret_cannot_control_failure_classification(handler: bool) -> None:
    diagnostic = json.dumps({"message": "opaque-\x1b[31m429\x1b[0m-value"})
    env = {"PROVIDER_API_KEY": "opaque-429-value"}
    if handler:
        failure = handler_failure_message(ValueError(diagnostic), env=env, log_path="task.log")
    else:
        failure = command_failure_message(
            1, stdout=diagnostic, stderr=None, env=env, log_path="task.log"
        )
    assert "[redacted]" in failure.error
    assert "429" not in failure.error
    assert failure.failure_class is not FailureClass.RATE_LIMITED


@pytest.mark.parametrize("with_secret", [False, True])
def test_normalizing_json_does_not_turn_unicode_into_a_status_code(with_secret: bool) -> None:
    message = "ValueError Щ C:\\file"
    if with_secret:
        message += " opaque-\x1b[31mprivate\x1b[0m-value"
    diagnostic = json.dumps({"message": message}, ensure_ascii=False)
    failure = command_failure_message(
        1,
        stdout=diagnostic,
        stderr=None,
        env={"PROVIDER_API_KEY": "opaque-private-value"},
        log_path="task.log",
    )
    assert "Щ" in failure.error
    assert "429" not in failure.error
    assert failure.failure_class is FailureClass.CRASH
    if with_secret:
        assert "[redacted]" in failure.error


def test_empty_handler_exception_retains_its_type_without_inventing_a_cause() -> None:
    failure = handler_failure_message(ValueError(), env={}, log_path="task.log")
    assert failure.error == "ValueError (traceback: task.log)"
    assert failure.failure_class is FailureClass.UNKNOWN


def test_failure_cause_drops_only_the_trailing_evidence_path() -> None:
    command = command_failure_message(
        1,
        stdout=None,
        stderr="ProviderError: HTTP 503 (traceback: remote)",
        env={},
        log_path=".logs/tasks/query/alfa/process_att-1.log",
    ).error
    assert (
        failure_cause(command)
        == "command exit code 1 (stderr: ProviderError: HTTP 503 (traceback: remote))"
    )
    handler = handler_failure_message(
        RuntimeError("upstream said; log: remote)"),
        env={},
        log_path=".logs/tasks/query/brvo/process_att-2.log",
    ).error
    assert failure_cause(handler) == "RuntimeError: upstream said; log: remote)"
    for unchanged in (
        "exit code 1 (log: API Error: overloaded)",
        "timeout after 600s",
        "output validation failed: report: path does not exist: report.md",
    ):
        assert failure_cause(unchanged) == unchanged


def test_without_evidence_paths_drops_every_nested_attempt_path() -> None:
    """A composite's error nests each failed child's message and its log path."""
    child = handler_failure_message(
        RuntimeError("staging refused"),
        env={},
        log_path=".logs/tasks/finalize/process_att-20260922T105447Z.1.aaa.log",
    ).error
    command = command_failure_message(
        1, stdout=None, stderr="boom", env={}, log_path=".logs/tasks/fetch/process_att-2.log"
    ).error
    nested = (
        f"Process completed with failures: finalize: {child}; fetch: {command}. "
        "Blocked: none (traceback: .logs/tasks/research/BB/process_att-3.log)"
    )
    assert without_evidence_paths(nested) == (
        "Process completed with failures: finalize: RuntimeError: staging refused; "
        "fetch: command exit code 1 (stderr: boom). Blocked: none"
    )
    # Text that only resembles evidence is the message itself and stays.
    for unchanged in (
        "ProviderError: HTTP 503 (traceback: remote)",
        "RuntimeError: upstream said; log: remote)",
        "timeout after 600s",
    ):
        assert without_evidence_paths(unchanged) == unchanged


def test_identical_item_failures_share_one_cause_across_evidence_paths() -> None:
    errors = [
        command_failure_message(
            1,
            stdout=None,
            stderr="ProviderError: HTTP 503 Service Unavailable",
            env={},
            log_path=f".logs/tasks/query/{key}/process_att-{key}.log",
        ).error
        for key in ("alfa", "brvo", "chrl")
    ]
    assert summarize_failure_causes(errors) == (
        "3 x command exit code 1 (stderr: ProviderError: HTTP 503 Service Unavailable)"
    )


def test_many_distinct_causes_render_the_most_frequent_within_a_bounded_summary() -> None:
    errors = ["timeout after 600s"] * 3 + ["exit code 1"] * 2
    errors += [
        handler_failure_message(
            RuntimeError(f"item {index}: " + "x" * 1_150),
            env={},
            log_path=f".logs/tasks/query/item-{index}/process_att-{index}.log",
        ).error
        for index in range(200)
    ]
    summary = summarize_failure_causes(errors)
    assert summary.startswith("3 x timeout after 600s; 2 x exit code 1; 1 x RuntimeError: item ")
    assert len(summary) <= 4_000
    assert "process_att-" not in summary
    omitted = int(summary.rsplit("; and ", 1)[1].split(" ", 1)[0])
    shown = summary.count(" x ")
    assert shown + omitted == 202
    assert summary.endswith(f"and {omitted} more causes ({omitted} items)")
