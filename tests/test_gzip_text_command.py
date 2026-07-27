"""Tests for the reusable gzip-text sweep."""

from __future__ import annotations

import gzip
from pathlib import Path

from metaproc.commands.gzip_text import gzip_text_path


def test_gzip_text_path_filters_extensions_and_min_size(tmp_path: Path) -> None:
    large_log = tmp_path / "large.log"
    large_log.write_text("plain log line with repeated text\n" * 100)
    small_log = tmp_path / "small.log"
    small_log.write_text("small\n")
    json_artifact = tmp_path / "artifact.json"
    json_artifact.write_text("{}\n" * 100)

    summary = gzip_text_path(
        tmp_path,
        include=".log",
        min_size=200,
        exclude_active=False,
    )

    large_gz = large_log.with_suffix(".log.gz")
    assert summary.gzipped == 1
    assert summary.failed == 0
    assert not large_log.exists()
    assert large_gz.exists()
    with gzip.open(large_gz, "rt") as fh:
        assert "plain log line" in fh.read()
    assert small_log.exists()
    assert json_artifact.exists()
