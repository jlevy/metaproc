"""A declared for_each.retry budget re-attempts transient code-handler failures.

Code handlers have no adapter-level operational retry beneath them, so a handler that
raises on a rate limit, a 5xx, a timeout, or a connection reset must be retried by the
step's declared budget. A hard quota, an ordinary handler bug, and a bare nonzero exit
must not be. The decision is made once, from the full exception, when the attempt is
recorded; the retry wrapper reads the recorded disposition back.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.engine.command_diagnostics import handler_failure_message
from metaproc.engine.retry import FailureClass, RetryVerdict, is_transient_failure
from metaproc.io.state_io import read_attempt_history_at
from metaproc.models.runtime import AttemptDisposition
from metaproc.paths import STATE_DIR, TASKS_SUBDIR

# ── Classification of handler exceptions ─────────────────────────────


class _StatusError(Exception):
    """An SDK error that carries its HTTP status the way OpenAI and Anthropic do."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _CodeError(Exception):
    """An SDK error that carries its HTTP status as ``code``, as google-genai does."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _ResponseError(Exception):
    """An error that carries a response object, as httpx and requests do."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.response = _Response(status_code)


class TransportError(Exception):
    """Stands in for httpx's transport base class, matched by name."""


class RemoteProtocolError(TransportError):
    """Stands in for httpx.RemoteProtocolError, whose message names no pattern."""


def _wrapped(cause: Exception) -> RuntimeError:
    try:
        raise cause
    except Exception as exc:
        try:
            raise RuntimeError("transcribe failed for item abc") from exc
        except RuntimeError as wrapped:
            return wrapped
    raise AssertionError("unreachable")


@pytest.mark.parametrize(
    ("exception", "failure_class"),
    [
        (_StatusError("Error code: 503 - overloaded", 503), FailureClass.SERVER_ERROR),
        (_StatusError("upstream said no", 429), FailureClass.RATE_LIMITED),
        (_CodeError("RESOURCE_EXHAUSTED. Please try again later.", 429), FailureClass.RATE_LIMITED),
        (_CodeError("DEADLINE_EXCEEDED", 504), FailureClass.TIMEOUT),
        (_ResponseError("bad gateway", 502), FailureClass.SERVER_ERROR),
        (_ResponseError("upstream request expired", 408), FailureClass.TIMEOUT),
        (ConnectionResetError(54, "Connection reset by peer"), FailureClass.SERVER_ERROR),
        (BrokenPipeError(32, "Broken pipe"), FailureClass.SERVER_ERROR),
        (TimeoutError("The read operation timed out"), FailureClass.TIMEOUT),
        (
            RemoteProtocolError("Server disconnected without sending a response."),
            FailureClass.SERVER_ERROR,
        ),
        (_wrapped(_StatusError("Too Many Requests", 429)), FailureClass.RATE_LIMITED),
        (_wrapped(RemoteProtocolError("peer closed")), FailureClass.SERVER_ERROR),
        (
            _CodeError(
                "Quota exceeded for aiplatform.googleapis.com/"
                "generate_content_requests_per_minute_per_project_per_base_model",
                429,
            ),
            FailureClass.QUOTA_EXHAUSTED,
        ),
    ],
)
def test_transient_handler_exceptions_are_retryable(
    exception: Exception, failure_class: FailureClass
) -> None:
    failure = handler_failure_message(exception, env={}, log_path="task.log")
    assert failure.failure_class is failure_class
    assert failure.verdict is RetryVerdict.RETRY
    assert is_transient_failure(failure.failure_class, failure.verdict)


@pytest.mark.parametrize(
    "exception",
    [
        ValueError("model returned malformed JSON"),
        KeyError("text"),
        FileNotFoundError(2, "No such file or directory", "/data/page-500.png"),
        ValueError("invalid page count: 500"),
        KeyError("connection"),
        _StatusError("invalid argument", 400),
        _CodeError("PERMISSION_DENIED", 403),
        # Structure never overrides a permanent signal in the text.
        _CodeError("Quota exceeded: requests per day", 429),
        _StatusError("billing account disabled", 503),
        _wrapped(ValueError("schema mismatch")),
    ],
)
def test_permanent_handler_exceptions_are_not_retryable(exception: Exception) -> None:
    failure = handler_failure_message(exception, env={}, log_path="task.log")
    assert not is_transient_failure(failure.failure_class, failure.verdict)


@pytest.mark.parametrize("status", [400, 401, 403])
@pytest.mark.parametrize("explicit_cause", [False, True])
def test_terminal_http_status_overrides_earlier_timeout(status: int, explicit_cause: bool) -> None:
    try:
        try:
            raise TimeoutError("request expired")
        except TimeoutError as exc:
            refusal = _StatusError("request refused", status)
            if explicit_cause:
                raise refusal from exc
            raise refusal  # noqa: B904 - exercise implicit exception context
    except _StatusError as exception:
        failure = handler_failure_message(exception, env={}, log_path="task.log")
    assert failure.verdict is RetryVerdict.FAIL
    assert not is_transient_failure(failure.failure_class, failure.verdict)


@pytest.mark.parametrize(
    ("message", "failure_class"),
    [
        ("HTTP 429 rate exceeded", FailureClass.RATE_LIMITED),
        ("503 UNAVAILABLE. Please try again later.", FailureClass.SERVER_ERROR),
        ("504 DEADLINE_EXCEEDED", FailureClass.TIMEOUT),
        (
            "TPM quota exhausted. No spare capacity is currently available",
            FailureClass.QUOTA_EXHAUSTED,
        ),
    ],
)
def test_explicit_provider_diagnostics_remain_retryable(
    message: str, failure_class: FailureClass
) -> None:
    failure = handler_failure_message(RuntimeError(message), env={}, log_path="task.log")
    assert (failure.failure_class, failure.verdict) == (failure_class, RetryVerdict.RETRY)


def test_a_status_in_the_evidence_path_does_not_make_a_bug_transient() -> None:
    failure = handler_failure_message(
        ValueError("bad row"), env={}, log_path=".logs/tasks/s/item-429/process_x.log"
    )
    assert failure.failure_class is FailureClass.UNKNOWN
    assert failure.verdict is RetryVerdict.FAIL


@pytest.mark.parametrize(
    ("failure_class", "verdict", "expected"),
    [
        (FailureClass.RATE_LIMITED, RetryVerdict.RETRY, True),
        (FailureClass.SERVER_ERROR, RetryVerdict.RETRY, True),
        (FailureClass.TIMEOUT, RetryVerdict.RETRY, True),
        (FailureClass.QUOTA_EXHAUSTED, RetryVerdict.RETRY, True),
        (FailureClass.QUOTA_EXHAUSTED, RetryVerdict.FAIL, False),
        (FailureClass.RATE_LIMITED, RetryVerdict.FAIL, False),
        (FailureClass.CRASH, RetryVerdict.RETRY, False),
        (FailureClass.UNKNOWN, RetryVerdict.FAIL, False),
        (FailureClass.INVALID_OUTPUT, RetryVerdict.RETRY, False),
    ],
)
def test_transience_needs_a_transient_class_and_a_retry_verdict(
    failure_class: FailureClass, verdict: RetryVerdict, expected: bool
) -> None:
    assert is_transient_failure(failure_class, verdict) is expected


# ── End to end through run-process ──────────────────────────────────

_HANDLERS = '''\
"""Handlers that fail on an item's first attempt, the way a rate-limited call does."""

from __future__ import annotations

from pathlib import Path


class ClientError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def _first_attempt(variables: dict[str, str], name: str) -> bool:
    log = Path(variables["RUNS_DIR"]) / "invocations.log"
    seen = log.read_text().splitlines() if log.exists() else []
    with log.open("a") as fh:
        fh.write(f"{name}:{variables['item']}\\n")
    return f"{name}:{variables['item']}" not in seen


def _write(variables: dict[str, str], name: str) -> None:
    out = Path(variables["RUNS_DIR"]) / variables["RUN_ID"] / name / f"{variables['item']}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(variables["item"] + "\\n")


def rate_limited(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    if _first_attempt(variables, "rate_limited"):
        raise ClientError("429 RESOURCE_EXHAUSTED. Please try again later.", 429)
    _write(variables, "rate_limited")


def buggy(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    _first_attempt(variables, "buggy")
    raise ValueError("invalid page count: 500")
'''

_ROSTER = """\
---
progress:
  schema: metaproc:ProgressSpec/0.1
  process: transient-retry
  items:
    - item: alfa
    - item: brvo
    - item: chrl
---
# Roster
"""


def _process(handler: str, *, retry: bool) -> str:
    retry_block = (
        "        retry: { max_retries: 2, initial_backoff_s: 0, max_backoff_s: 0 }\n"
        if retry
        else ""
    )
    return (
        textwrap.dedent(
            f"""\
            ---
            process:
              name: transient-retry
              deps:
                roster: {{ path: ./roster.md, as: path }}
              steps:
                - id: call
                  mode: code
                  handler: "handlers.py:{handler}"
                  for_each:
                    over: deps.roster
                    bind: item
                    bind_fields: [item]
                    key: "{{{{item}}}}"
            """
        )
        + retry_block
        + textwrap.indent(
            textwrap.dedent(
                f"""\
                outputs:
                  out: {{ path: "{{{{run.dir}}}}/{handler}/{{{{item}}}}.txt", kind: file }}
                """
            ),
            "      ",
        )
        + "---\n# Transient Retry\n"
    )


def _run(tmp_path: Path, handler: str, *, retry: bool) -> tuple[int, list[str], Path]:
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "handlers.py").write_text(_HANDLERS)
    (process_dir / "roster.md").write_text(_ROSTER)
    process_path = process_dir / "transient-retry.process.md"
    process_path.write_text(_process(handler, retry=retry))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--var",
            f"RUNS_DIR={runs_dir}",
            "--var",
            "RUN_ID=r1",
        ],
    )
    invocations = (runs_dir / "invocations.log").read_text().splitlines()
    return result.exit_code, invocations, runs_dir / "r1"


def test_a_rate_limited_handler_is_retried_under_the_declared_budget(tmp_path: Path) -> None:
    exit_code, invocations, run_dir = _run(tmp_path, "rate_limited", retry=True)
    assert exit_code == 0
    assert sorted(invocations) == sorted(
        f"rate_limited:{item}" for item in ("alfa", "alfa", "brvo", "brvo", "chrl", "chrl")
    )
    history = read_attempt_history_at(run_dir / STATE_DIR / TASKS_SUBDIR / "call" / "alfa")
    assert [attempt.disposition for attempt in history] == [
        AttemptDisposition.retryable,
        AttemptDisposition.succeeded,
    ]
    assert history[0].failure_class == str(FailureClass.RATE_LIMITED)


def test_a_rate_limited_handler_without_a_declared_budget_runs_once(tmp_path: Path) -> None:
    exit_code, invocations, run_dir = _run(tmp_path, "rate_limited", retry=False)
    assert exit_code != 0
    assert len(invocations) == 3
    history = read_attempt_history_at(run_dir / STATE_DIR / TASKS_SUBDIR / "call" / "alfa")
    assert [attempt.disposition for attempt in history] == [AttemptDisposition.permanent]


def test_a_handler_bug_is_not_retried(tmp_path: Path) -> None:
    exit_code, invocations, run_dir = _run(tmp_path, "buggy", retry=True)
    assert exit_code != 0
    assert sorted(invocations) == ["buggy:alfa", "buggy:brvo", "buggy:chrl"]
    history = read_attempt_history_at(run_dir / STATE_DIR / TASKS_SUBDIR / "call" / "brvo")
    assert [attempt.disposition for attempt in history] == [AttemptDisposition.permanent]
