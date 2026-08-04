"""Shared test fixtures for metaproc tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metaproc.config.providers import providers_with_api_keys
from metaproc.runpool.pool import RunPoolConfig

# Provide a placeholder RUNS_DIR so CLI tests pass the
# require_runtime_runs_dir guard added in
# plan-2026-05-20-earnings-run-settings-migration.md. Tests that need a
# specific value (e.g. test_resolve_templates_env_var_fallback) still
# override via monkeypatch.setenv; tests that just want the CLI to get
# past the requirement check inherit this default. Dev shells typically
# set RUNS_DIR via .envrc; CI does not. resolve_run_path does not
# require the directory to exist.
os.environ.setdefault("RUNS_DIR", str(Path(__file__).parent / "_test-runs-placeholder"))


def _collapse_runpool_poll_intervals() -> None:
    """Collapse RunPool's wall-clock poll intervals for the test session.

    The production defaults are right for supervising real, long-lived
    subprocesses: RunPool wakes every ``monitor_interval_s`` (10s) to reap
    exited processes, every ``pressure_check_interval_s`` (10s) to sample
    host pressure, and every ``host_admission_poll_interval_s`` (1s) while
    waiting for an admission slot. Each of those is a literal
    ``asyncio.sleep``.

    A test that submits a MockBackend process still waits a full monitor
    cycle for the pool to notice the (already finished) process exited, so
    it costs ~10s of pure sleeping regardless of how little work it does.
    Suites that knew about this set the intervals by hand
    (``test_runpool_pool.py``, ``test_runpool_integration.py``,
    ``test_pool_composite_integration.py``); the ones that did not
    (``test_runpool_lanes.py``, ``test_inline_retry_e2e.py``) paid 62s of
    wall clock for 2.7s of CPU across 16 tests.

    Lowering the *defaults* fixes both today's slow tests and every future
    one, and cannot mask a regression: an explicit value on a
    ``RunPoolConfig`` still wins, so tests that assert real timing
    behavior are untouched. Production is unaffected — this only runs
    under pytest.

    Timing-sensitive tests should keep setting these explicitly rather
    than relying on either the fast or the slow default.
    """
    fast_defaults = {
        "monitor_interval_s": 0.05,
        "pressure_check_interval_s": 0.05,
        "host_admission_poll_interval_s": 0.05,
    }
    changed = False
    for name, value in fast_defaults.items():
        field = RunPoolConfig.model_fields.get(name)
        if field is None:
            # The field was renamed upstream; leave the rest alone rather
            # than guessing, so the omission shows up as a slow suite and
            # not as a silently skipped knob.
            continue
        field.default = value
        changed = True
    if changed:
        RunPoolConfig.model_rebuild(force=True)


_collapse_runpool_poll_intervals()


def pytest_addoption(parser):
    """Register --update-golden CLI flag for golden test management."""
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Update golden QA report files",
    )


@pytest.fixture(autouse=True)
def _clear_gh_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub GH_TOKEN vars from the test env.

    The Batch dispatch code refuses to submit a job when GH_TOKEN is set
    without METAPROC_GCP_SECRET_GH_TOKEN (to prevent plaintext leakage).
    Dev shells often have GH_TOKEN exported for gh CLI, so clear both
    vars by default so tests see a clean slate and opt in explicitly.
    """
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("METAPROC_GCP_SECRET_GH_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _clear_claude_creds_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub Claude Code credential vars from the test env so dispatcher
    and adapter tests get a clean slate. Mirrors the GH_TOKEN scrub."""
    monkeypatch.delenv("CLAUDE_CODE_CREDS_JSON", raising=False)
    monkeypatch.delenv("METAPROC_GCP_SECRET_CLAUDE_CREDS", raising=False)


@pytest.fixture(autouse=True)
def _clear_openai_codex_creds_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub Codex credential and CODEX_HOME vars from the test env so
    dispatcher tests get a clean slate. Per-provider API key scrubs (e.g.
    OPENAI_API_KEY, DEEPSEEK_API_KEY, MOONSHOT_API_KEY, ANTHROPIC_API_KEY,
    GEMINI_API_KEY) and their METAPROC_GCP_SECRET_* refs are handled by
    `_clear_provider_credential_env` below — that one derives the list
    from the central provider registry so adding a new provider needs no
    edits here.

    CODEX_HOME is scrubbed because, when set, it would override the
    default ~/.codex resolution in resolve_codex_home() and
    cross-contaminate adapter / codex-auth tests that monkeypatch
    Path.home()."""
    monkeypatch.delenv("CODEX_CREDS_JSON", raising=False)
    monkeypatch.delenv("METAPROC_GCP_SECRET_CODEX_CREDS", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)


@pytest.fixture(autouse=True)
def _isolate_vehicle_b_lock_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Isolate the V-B per-label lock dir per-test (Phase 5).

    SlotCoordinator.acquire_slot creates a mkdir-based per-label lock
    in ``METAPROC_AUTH_POOL_LOCK_DIR`` (or ``~/.metaproc/auth-pool/locks``
    by default) for Vehicle B leases. The lock survives across test
    runs at the user-home default, so a test that doesn't tear down its
    lease wedges every subsequent test acquiring the same label.
    Pointing the dir at a fresh tmp path per test keeps tests isolated.

    Also lower the timeout to a few seconds so any test that does
    deadlock fails fast with MkdirLockTimeoutError instead of pytest's
    default 5-min timeout.
    """
    lock_dir = tmp_path_factory.mktemp("auth-pool-locks")
    monkeypatch.setenv("METAPROC_AUTH_POOL_LOCK_DIR", str(lock_dir))
    monkeypatch.setenv("METAPROC_AUTH_POOL_LOCK_TIMEOUT_S", "5")


@pytest.fixture(autouse=True)
def _disable_dotenv_loading(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop the typer callback from re-injecting the operator's `.env`.

    `metaproc.cli._load_dotenv` walks up from cwd on every CLI invocation
    and `os.environ.setdefault`s every key it finds — defeating per-test
    `monkeypatch.delenv` / `patch.dict("os.environ", {}, clear=True)`
    isolation. CI has no `.env` so the leak is invisible there, but a
    dev box with a populated `.env` (METAPROC_GCP_PROJECT, RUNS_DIR,
    METAPROC_GCP_SECRET_CLAUDE_CREDS, …) leaks those into tests that
    assume a clean env. Disable the loader for all tests; tests that
    need env vars must `monkeypatch.setenv` them explicitly.

    Exception: `test_cli_dotenv.py` exercises the loader itself.
    """
    if request.node.path.name == "test_cli_dotenv.py":
        return
    monkeypatch.setattr("metaproc.cli._load_dotenv", lambda: None)


@pytest.fixture(autouse=True)
def _clear_provider_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub every registry-managed provider credential from the test
    env (plaintext API key + matching METAPROC_GCP_SECRET_* ref).

    Without this scrub, an operator's `.env` (commonly setting
    OPENAI_API_KEY / DEEPSEEK_API_KEY / MOONSHOT_API_KEY for live runs)
    leaks into tests that only monkeypatch the historical subset of
    keys, producing flaky pollution failures: pi-cli's `check_auth`
    sees real creds when tests assume none, and
    `_build_secret_env_vars` raises plaintext-leakage RuntimeErrors
    because the matching SECRET ref is unset.

    The list is derived from `metaproc.config.providers.PROVIDERS` so
    adding a new provider in that file automatically extends this
    scrub — never edit this fixture's body when wiring a new provider."""

    for spec in providers_with_api_keys():
        if spec.api_key_env is not None:
            monkeypatch.delenv(spec.api_key_env.name, raising=False)
        if spec.gcp_secret_ref_env is not None:
            monkeypatch.delenv(spec.gcp_secret_ref_env.name, raising=False)
