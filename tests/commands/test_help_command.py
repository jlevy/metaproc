from __future__ import annotations

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.help import _should_render
from metaproc.docs import load_help_topics

runner = CliRunner()


def test_help_topics_load_all_bundled_docs() -> None:
    topics = load_help_topics()
    assert "Metaproc Developer Guide" in topics.developer
    assert topics.operator.strip()
    assert topics.concepts.strip()


def test_should_render_raw_forces_raw() -> None:
    # --raw wins even on a TTY.
    assert _should_render(raw=True, pager=False, tty=True) is False


def test_should_render_pager_forces_render() -> None:
    # --pager wins even off a TTY.
    assert _should_render(raw=False, pager=True, tty=False) is True


def test_should_render_follows_tty_by_default() -> None:
    assert _should_render(raw=False, pager=False, tty=True) is True
    assert _should_render(raw=False, pager=False, tty=False) is False


def test_help_no_topic_lists_topics() -> None:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    assert "operator" in result.stdout
    assert "developer" in result.stdout
    assert "concepts" in result.stdout


def test_help_topic_raw_outputs_markdown() -> None:
    result = runner.invoke(app, ["help", "developer", "--raw"])
    assert result.exit_code == 0
    assert "Metaproc Developer Guide" in result.stdout
    assert not result.stdout.startswith("---")


def test_help_unknown_topic_errors() -> None:
    result = runner.invoke(app, ["help", "nonsense"])
    assert result.exit_code == 2
