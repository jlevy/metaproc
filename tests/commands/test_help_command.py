from __future__ import annotations

from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.help import _should_render
from metaproc.docs import (
    TOPIC_DESCRIPTIONS,
    TOPIC_REGISTRY,
    load_help_topics,
    topic_markdown,
)

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


def test_every_registry_topic_serves_a_bundled_doc() -> None:
    # A topic whose file is missing from the wheel is only discoverable by asking
    # for it, which is exactly when it is most expensive to find out.
    for topic in TOPIC_REGISTRY:
        assert topic_markdown(topic.name).strip(), topic.name


def test_registry_word_counts_stay_close_to_the_documents() -> None:
    # The listing tells a reader what a topic costs before they spend context on
    # it, so a stale count is a wrong answer rather than cosmetic drift. The
    # tolerance is deliberately loose: prose edits should not fail the suite, but a
    # document that doubles or halves should.
    drifted = [
        (topic.name, topic.words, actual)
        for topic in TOPIC_REGISTRY
        if abs((actual := len(topic_markdown(topic.name).split())) - topic.words)
        > max(200, topic.words * 0.10)
    ]
    assert not drifted, f"update Topic.words in metaproc.docs: {drifted}"


def test_topic_listing_shows_names_and_sizes() -> None:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    for topic in TOPIC_REGISTRY:
        assert topic.name in result.stdout
    assert "words" in result.stdout


def test_unknown_topic_exits_two() -> None:
    result = runner.invoke(app, ["help", "no-such-topic"])
    assert result.exit_code == 2


def test_topic_descriptions_match_the_registry() -> None:
    # commands/help.py and skill/builtin.py both read this; drift between them is
    # how the CLI and the Agent Skill start describing different products.
    assert TOPIC_DESCRIPTIONS == {t.name: t.description for t in TOPIC_REGISTRY}
