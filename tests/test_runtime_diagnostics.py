"""Public diagnostic summaries for domain handlers and recoverable output records."""

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
