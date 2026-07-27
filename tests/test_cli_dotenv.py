"""Tests for the .env loader in metaproc.cli._load_dotenv.

The canonical ``.env.example`` template (regenerated from
``MetaprocEnv``) emits ``export VAR=value`` lines so a developer can
``set -a; source .env`` from shell. The CLI auto-loader must accept the
same shape — without the export-prefix tolerance, every operator who
copied .env.example silently lost auto-loaded env vars and the
auth-pool dispatch path failed at run time with
``METAPROC_GCP_PROJECT is not set``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metaproc.cli import _load_dotenv


@pytest.fixture
def isolated_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run_loader_with(env_text: str, monkeypatch) -> None:
    """Drop a .env in cwd with *env_text* and run _load_dotenv."""

    Path(".env").write_text(env_text)
    _load_dotenv()


class TestExportPrefixHandling:
    def test_export_prefix_is_stripped(self, isolated_cwd, monkeypatch):
        monkeypatch.delenv("FOO_PROJECT", raising=False)
        _run_loader_with("export FOO_PROJECT=test-project\n", monkeypatch)
        assert os.environ["FOO_PROJECT"] == "test-project"

    def test_bare_assignment_still_works(self, isolated_cwd, monkeypatch):
        # The pre-existing path for hand-edited .envs without `export `.
        monkeypatch.delenv("FOO_BARE", raising=False)
        _run_loader_with("FOO_BARE=plain-value\n", monkeypatch)
        assert os.environ["FOO_BARE"] == "plain-value"

    def test_mixed_lines(self, isolated_cwd, monkeypatch):
        monkeypatch.delenv("FOO_EXP", raising=False)
        monkeypatch.delenv("FOO_BARE_2", raising=False)
        _run_loader_with(
            "export FOO_EXP=exp-value\nFOO_BARE_2=bare-value\n",
            monkeypatch,
        )
        assert os.environ["FOO_EXP"] == "exp-value"
        assert os.environ["FOO_BARE_2"] == "bare-value"

    def test_existing_env_wins_over_dotenv(self, isolated_cwd, monkeypatch):
        # setdefault semantics: shell-set vars take precedence over .env.
        monkeypatch.setenv("FOO_PRESET", "shell-value")
        _run_loader_with("export FOO_PRESET=dotenv-value\n", monkeypatch)
        assert os.environ["FOO_PRESET"] == "shell-value"

    def test_comment_and_blank_lines_skipped(self, isolated_cwd, monkeypatch):
        monkeypatch.delenv("FOO_COMMENTED", raising=False)
        _run_loader_with(
            "# top comment\n\n  # indented comment\nexport FOO_COMMENTED=ok\n",
            monkeypatch,
        )
        assert os.environ["FOO_COMMENTED"] == "ok"

    def test_export_with_extra_whitespace(self, isolated_cwd, monkeypatch):
        # `export   VAR=value` (extra whitespace after export) — the
        # template emits exactly one space, but a hand-edit might add
        # more. The loader uses lstrip() after stripping the prefix so
        # tabs/multiple spaces are tolerated.
        monkeypatch.delenv("FOO_WS", raising=False)
        _run_loader_with("export   FOO_WS=ok\n", monkeypatch)
        assert os.environ["FOO_WS"] == "ok"
