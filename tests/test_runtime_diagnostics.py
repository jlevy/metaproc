"""Public diagnostic summaries for domain handlers and recoverable output records."""

import json

import pytest

from metaproc.runtime.diagnostics import summarize_diagnostic


@pytest.mark.parametrize("terminator", ["\x1b\\", "\x07"])
def test_hyperlink_controls_are_removed_before_matching_a_secret(terminator: str) -> None:
    diagnostic = (
        f"unlabeled opaque-\x1b]8;;https://example.invalid{terminator}"
        f"private\x1b]8;;{terminator}-value"
    )
    summary = summarize_diagnostic(diagnostic, env={"PROVIDER_API_KEY": "opaque-private-value"})
    assert summary == "unlabeled [redacted]"


def test_nonprinting_controls_cannot_split_a_secret_or_reach_persisted_text() -> None:
    summary = summarize_diagnostic(
        "unlabeled opaque-\x00priv\x08ate-value",
        env={"PROVIDER_API_KEY": "opaque-private-value"},
    )
    assert summary == "unlabeled [redacted]"


@pytest.mark.parametrize(
    "middle",
    [
        "\x1b[31mprivate\x1b[0m",
        "\x1b]8;;https://example.invalid\x07private\x1b]8;;\x07",
        "\x1b]8;;https://example.invalid\x1b\\private\x1b]8;;\x1b\\",
        "\x9d8;;https://example.invalid\x9cprivate\x9d8;;\x9c",
        "priv\x00\x08ate",
    ],
)
@pytest.mark.parametrize("encoding_layers", [1, 2, 3])
def test_encoded_terminal_controls_cannot_hide_a_known_secret(
    middle: str, encoding_layers: int
) -> None:
    diagnostic = f"opaque-{middle}-value"
    for _ in range(encoding_layers):
        diagnostic = json.dumps({"message": diagnostic})
    summary = summarize_diagnostic(
        f"provider response: {diagnostic}\nHTTP 403 denied",
        env={"PROVIDER_API_KEY": "opaque-private-value"},
    )
    assert "[redacted]" in summary
    for fragment in ("opaque-", "private", "-value", "example.invalid", "\\u001b", "\x1b"):
        assert fragment not in summary
    assert summary.endswith("HTTP 403 denied")


def test_json_escaped_secret_is_matched_as_a_value_without_rewriting_other_escapes() -> None:
    diagnostic = r'provider response: {"message": "opaque-\u0070rivate-value", "path": "C:\\users\\temp", "literal": "\\u001b", "unicode": "\ud800"}'
    summary = summarize_diagnostic(diagnostic, env={"PROVIDER_API_KEY": "opaque-private-value"})
    assert summary == diagnostic.replace(r"opaque-\u0070rivate-value", "[redacted]")


def test_redacted_json_with_a_lone_surrogate_remains_utf8_writable() -> None:
    diagnostic = json.dumps({"message": "\ud800 opaque-\x1b[31mprivate\x1b[0m-value"})
    summary = summarize_diagnostic(diagnostic, env={"PROVIDER_API_KEY": "opaque-private-value"})
    assert summary.encode("utf-8")
    assert json.loads(summary)["message"] == "\ud800 [redacted]"


@pytest.mark.parametrize("explicit_env", [False, True])
def test_summary_redacts_before_clipping_without_adding_execution_facts(
    monkeypatch: pytest.MonkeyPatch, explicit_env: bool
) -> None:
    secret = "opaque-private-" + "s" * 2_000
    env = {"PROVIDER_API_KEY": secret, "PUBLIC_KEY_ID": "429", "AUTH_TIMEOUT_S": "17"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    diagnostic = (
        "omitted progress\n" * 20
        + "unlabeled "
        + secret.replace("private", "\x1b[31mprivate\x1b[0m")
        + "\nHTTP 429; Retry-After: 17"
    )
    summary = summarize_diagnostic(diagnostic, env=env if explicit_env else None)
    assert summary.startswith("[truncated]\n")
    assert summary.endswith("unlabeled [redacted]\nHTTP 429; Retry-After: 17")
    assert len(summary) < 1_300
    for value in ("opaque-", "private", "\x1b", "exit code", "log:"):
        assert value not in summary
