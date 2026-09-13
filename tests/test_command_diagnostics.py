"""Credential handling and missing-evidence behavior for durable command errors."""

from metaproc.engine.command_diagnostics import command_failure_message


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
    )
    assert "ProviderError: HTTP 403 denied" in error
    assert "command exit code 3" in error
    assert "log: task.log" in error
    for value in ("opaque-secret", "response-secret", "user:password", "query-secret", "dXNlcj"):
        assert value not in error
    assert "[redacted]" in error
    assert len(error) < 1_000


def test_empty_capture_does_not_invent_a_diagnostic_or_log_file() -> None:
    error = command_failure_message(11, stdout="", stderr="\n", env={}, log_path="not-written.log")
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
    )
    assert "Retry-After: 17" in error
    assert "opaque-private-value" not in error


def test_giant_line_keeps_the_terminal_diagnostic_and_marks_omission() -> None:
    diagnostic = "ProviderError: HTTP 503 unavailable; Retry-After: 25"
    error = command_failure_message(
        4, stdout="x" * 20_000 + diagnostic, stderr=None, env={}, log_path="task.log"
    )
    assert "stdout:" in error
    assert diagnostic in error
    assert "[truncated]" in error
    assert len(error) < 1_400
