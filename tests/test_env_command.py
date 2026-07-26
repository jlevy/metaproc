"""Smoke tests for `metaproc env`.

Covers the three contracts the command asserts:
  1. Every registered env var appears in JSON output (no silent filter drops).
  2. SECRET values are redacted; non-secret values print raw.
  3. ``--kind`` and ``--name`` filters narrow the result set as advertised.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.config.env_vars import MetaprocEnv


def _invoke(runner: CliRunner, *args: str, env: dict[str, str] | None = None) -> str:
    result = runner.invoke(app, ["--format", "json", "env", *args], env=env or {})
    assert result.exit_code == 0, result.output
    return result.stdout


def test_env_lists_every_registered_member() -> None:
    runner = CliRunner()
    payload = json.loads(_invoke(runner))
    names = {row["name"] for row in payload}
    assert names == {m.name for m in MetaprocEnv}


def test_env_redacts_secret_values() -> None:
    runner = CliRunner()
    payload = json.loads(
        _invoke(runner, "--kind", "SECRET", env={"ANTHROPIC_API_KEY": "sk-ant-super-secret"})
    )
    row = next(r for r in payload if r["name"] == "ANTHROPIC_API_KEY")
    assert "super-secret" not in row["value"]
    assert row["value"].startswith("<set, len=")


def test_env_shows_non_secret_values_verbatim() -> None:
    runner = CliRunner()
    payload = json.loads(
        _invoke(runner, "--name", "METAPROC_GCP_PROJECT", env={"METAPROC_GCP_PROJECT": "my-proj"})
    )
    row = next(r for r in payload if r["name"] == "METAPROC_GCP_PROJECT")
    assert row["value"] == "my-proj"


def test_env_kind_filter_narrows_results() -> None:
    runner = CliRunner()
    payload = json.loads(_invoke(runner, "--kind", "SECRET"))
    assert payload
    assert {row["kind"] for row in payload} == {"SECRET"}


def test_env_name_filter_is_case_insensitive_substring() -> None:
    runner = CliRunner()
    payload = json.loads(_invoke(runner, "--name", "gcp_filestore"))
    assert payload
    for row in payload:
        assert "GCP_FILESTORE" in row["name"]


def test_env_rejects_unknown_kind() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["env", "--kind", "BOGUS"])
    assert result.exit_code != 0
    assert "BOGUS" in result.output or "--kind" in result.output


def test_env_only_set_filters_out_unset_vars() -> None:
    runner = CliRunner()
    payload = json.loads(_invoke(runner, "--only-set", env={"METAPROC_GCP_PROJECT": "p"}))
    names = {row["name"] for row in payload}
    assert "METAPROC_GCP_PROJECT" in names
    for row in payload:
        assert row["value"]
