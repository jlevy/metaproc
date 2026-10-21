"""Tests for the reusable gzip-text sweep and its codec selection."""

from __future__ import annotations

import gzip
from compression import zstd
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.gzip_text import compress_file, gzip_text_path
from metaproc.io.compression import CODECS, GZIP, ZSTD, Codec

runner = CliRunner()


def _large_log(tmp_path: Path) -> Path:
    large_log = tmp_path / "large.log"
    large_log.write_text("plain log line with repeated text\n" * 100)
    return large_log


def test_gzip_text_path_filters_extensions_and_min_size(tmp_path: Path) -> None:
    large_log = _large_log(tmp_path)
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
    assert summary.codec == "gzip"
    assert summary.compressed == 1
    assert summary.failed == 0
    assert not large_log.exists()
    assert large_gz.exists()
    with gzip.open(large_gz, "rt") as fh:
        assert "plain log line" in fh.read()
    assert small_log.exists()
    assert json_artifact.exists()


def test_gzip_text_path_writes_zstd_when_asked(tmp_path: Path) -> None:
    large_log = _large_log(tmp_path)
    original = large_log.read_bytes()

    summary = gzip_text_path(
        tmp_path, include=".log", min_size=200, codec=ZSTD, exclude_active=False
    )

    large_zst = large_log.with_suffix(".log.zst")
    assert summary.codec == "zstd"
    assert summary.compressed == 1
    assert not large_log.exists()
    assert large_zst.exists()
    assert zstd.decompress(large_zst.read_bytes()) == original
    # The writer pledged the size, so the frame header carries it.
    assert ZSTD.recorded_size(large_zst) == len(original)


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_compress_file_verifies_the_round_trip_and_keeps_mtime(
    tmp_path: Path, codec: Codec
) -> None:
    src = _large_log(tmp_path)
    original = src.read_bytes()
    mtime = src.stat().st_mtime
    result = compress_file(src, codec=codec, keep_original=True)
    dst = src.with_name(src.name + codec.suffix)
    assert result.codec == codec.name
    assert result.original_size == len(original)
    assert result.compressed_size == dst.stat().st_size < len(original)
    assert src.exists()
    assert abs(dst.stat().st_mtime - mtime) < 1
    with codec.open_read(dst) as fh:
        assert fh.read() == original
    assert not src.with_name(src.name + codec.partial_suffix).exists()


def test_sweep_skips_a_stream_already_compressed_in_another_codec(tmp_path: Path) -> None:
    large_log = _large_log(tmp_path)
    large_log.with_suffix(".log.zst").write_bytes(zstd.compress(b"earlier encoding"))

    summary = gzip_text_path(tmp_path, include=".log", min_size=200, exclude_active=False)

    assert summary.compressed == 0
    assert summary.skipped == 1
    assert large_log.exists()
    assert not large_log.with_suffix(".log.gz").exists()


def test_sweep_never_touches_compressed_files(tmp_path: Path) -> None:
    (tmp_path / "a.log.gz").write_bytes(gzip.compress(b"x" * 1000))
    (tmp_path / "b.log.zst").write_bytes(zstd.compress(b"x" * 1000))
    summary = gzip_text_path(tmp_path, include=".log,.gz,.zst", min_size=1, exclude_active=False)
    assert summary.matched == 0


def test_cli_codec_option_selects_zstd(tmp_path: Path) -> None:
    large_log = _large_log(tmp_path)
    result = runner.invoke(
        app,
        [
            "gzip-text",
            str(tmp_path),
            "--include",
            ".log",
            "--min-size",
            "200",
            "--codec",
            "zstd",
            "--no-exclude-active",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "compressed with zstd" in result.output
    assert large_log.with_suffix(".log.zst").exists()


def test_cli_rejects_an_unknown_codec_and_an_out_of_range_level(tmp_path: Path) -> None:
    _large_log(tmp_path)
    result = runner.invoke(app, ["gzip-text", str(tmp_path), "--codec", "brotli"])
    assert result.exit_code == 1
    assert "unknown compression codec" in result.output
    result = runner.invoke(app, ["gzip-text", str(tmp_path), "--level", "12"])
    assert result.exit_code == 1
    assert "gzip level must be between 1 and 9" in result.output
    assert GZIP.level_range == (1, 9)
