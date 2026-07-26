from __future__ import annotations

import json

import pytest

from metaproc.commands.helpers import load_item_contexts, resolve_process_path
from metaproc.errors import CLIError


def test_load_item_contexts_from_env(monkeypatch) -> None:
    contexts = [{"EVENT_ID": "AAPL", "TICKER": "AAPL"}]
    monkeypatch.setenv("METAPROC_ITEM_CONTEXTS", json.dumps(contexts))

    assert load_item_contexts() == contexts


def test_load_item_contexts_from_file(monkeypatch, tmp_path) -> None:
    contexts = [{"EVENT_ID": "MSFT", "TICKER": "MSFT"}]
    payload_path = tmp_path / "item-contexts.json"
    payload_path.write_text(json.dumps(contexts))
    monkeypatch.delenv("METAPROC_ITEM_CONTEXTS", raising=False)
    monkeypatch.setenv("METAPROC_ITEM_CONTEXTS_FILE", str(payload_path))

    assert load_item_contexts() == contexts


def test_load_item_contexts_missing_file_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("METAPROC_ITEM_CONTEXTS", raising=False)
    monkeypatch.setenv("METAPROC_ITEM_CONTEXTS_FILE", str(tmp_path / "nonexistent.json"))

    with pytest.raises(CLIError, match="not found"):
        load_item_contexts()


class TestResolveProcessPath:
    """resolve_process_path accepts both the file form and the directory form
    (playbooks and plan specs use the directory form for brevity)."""

    def test_file_form(self, tmp_path) -> None:
        spec = tmp_path / "predict.process.md"
        spec.write_text("---\nname: predict\n---\n")
        assert resolve_process_path(spec) == spec

    def test_directory_form_resolves_to_matching_spec(self, tmp_path) -> None:
        process_dir = tmp_path / "predict"
        process_dir.mkdir()
        spec = process_dir / "predict.process.md"
        spec.write_text("---\nname: predict\n---\n")
        assert resolve_process_path(process_dir) == spec

    def test_directory_form_missing_spec_raises(self, tmp_path) -> None:
        process_dir = tmp_path / "predict"
        process_dir.mkdir()
        # No predict.process.md inside
        with pytest.raises(CLIError, match="no matching spec file"):
            resolve_process_path(process_dir)

    def test_file_form_wrong_suffix_raises(self, tmp_path) -> None:
        wrong = tmp_path / "predict.md"
        wrong.write_text("")
        with pytest.raises(CLIError, match="must end in"):
            resolve_process_path(wrong)

    def test_file_form_missing_raises(self, tmp_path) -> None:
        missing = tmp_path / "predict.process.md"
        with pytest.raises(CLIError, match="not found"):
            resolve_process_path(missing)
