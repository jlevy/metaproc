"""Credential handling and missing-evidence behavior for durable command errors."""

import json

import pytest

from metaproc.engine.command_diagnostics import command_failure_message, handler_failure_message
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
        stderr="ProviderError: HTTP 429; Retry-After: 17; credentials are xy and ab",
        env={
            "AUTH_TIMEOUT_S": "17",
            "PUBLIC_KEY_ID": "429",
            "TOKEN_COUNT": "17",
            "CUSTOM_API_KEY": "ab",
            "METAPROC_GCP_SECRET_REFS_JSON": '{"CUSTOM_KEY_ID":"projects/example/secrets/id/versions/latest"}',
            "CUSTOM_KEY_ID": "xy",
        },
        log_path="task.log",
    ).error
    assert "HTTP 429; Retry-After: 17" in error
    assert "credentials are [redacted] and [redacted]" in error


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
