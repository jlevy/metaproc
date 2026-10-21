"""Tests for ``metaproc.io.compression``: the codec registry and each codec's contract."""

from __future__ import annotations

import gzip
import io
import os
import struct
from compression import zstd
from pathlib import Path

import pytest

from metaproc.io.compression import (
    CODECS,
    COMPRESSION_SUFFIXES,
    DEFAULT_WRITE_CODEC,
    GZIP,
    ZSTD,
    ArtifactDecodeError,
    Codec,
    codec_by_name,
    codec_for_path,
)

_DATA = b'{"event": "message", "text": "Hello \xe2\x80\x94 w\xc3\xb6rld"}\n' * 200


# ── Registry ─────────────────────────────────────────────────────


def test_registry_is_the_closed_set() -> None:
    assert [c.name for c in CODECS] == ["gzip", "zstd"]
    assert COMPRESSION_SUFFIXES == frozenset({".gz", ".zst"})
    assert DEFAULT_WRITE_CODEC in CODECS
    assert DEFAULT_WRITE_CODEC is GZIP


def test_codec_for_path_detects_by_suffix_only() -> None:
    assert codec_for_path(Path("a.jsonl.gz")) is GZIP
    assert codec_for_path(Path("a.jsonl.GZ")) is GZIP
    assert codec_for_path(Path("a.jsonl.zst")) is ZSTD
    assert codec_for_path(Path("a.jsonl")) is None
    assert codec_for_path(Path("a.jsonl.gz.partial")) is None
    assert codec_for_path(Path("a.tar.gz")) is GZIP


def test_codec_by_name_round_trips_and_rejects_unknown() -> None:
    for codec in CODECS:
        assert codec_by_name(codec.name) is codec
        assert codec_by_name(codec.name.upper()) is codec
    with pytest.raises(ValueError, match="gzip, zstd"):
        codec_by_name("brotli")


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_each_codec_states_its_facts(codec: Codec) -> None:
    assert codec.suffix.startswith(".")
    assert codec.partial_suffix == codec.suffix + ".partial"
    assert codec.content_encoding
    low, high = codec.level_range
    assert low <= codec.default_level <= high
    assert codec.check_level(None) == codec.default_level
    assert codec.check_level(low) == low
    with pytest.raises(ValueError, match="level"):
        codec.check_level(high + 1)
    assert codec.decode_errors


# ── Reading and writing ──────────────────────────────────────────


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_one_shot_compress_round_trips_through_open_read(tmp_path: Path, codec: Codec) -> None:
    path = tmp_path / f"blob{codec.suffix}"
    path.write_bytes(codec.compress(_DATA))
    with codec.open_read(path) as fh:
        assert fh.read() == _DATA


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_writer_records_the_decoded_size_when_pledged(tmp_path: Path, codec: Codec) -> None:
    path = tmp_path / f"blob{codec.suffix}"
    with path.open("wb") as raw:
        with codec.open_write(raw, source_size=len(_DATA), source_mtime=1_700_000_000.0) as out:
            for start in range(0, len(_DATA), 1000):
                out.write(_DATA[start : start + 1000])
    assert codec.recorded_size(path) == len(_DATA)
    with codec.open_read(path) as fh:
        assert fh.read() == _DATA


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_open_read_exposes_the_stored_files_descriptor(tmp_path: Path, codec: Codec) -> None:
    path = tmp_path / f"blob{codec.suffix}"
    path.write_bytes(codec.compress(_DATA))
    with codec.open_read(path) as fh:
        assert os.fstat(fh.fileno()).st_size == path.stat().st_size
        assert fh.name == str(path)


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_truncated_stream_raises_decode_error(tmp_path: Path, codec: Codec) -> None:
    whole = codec.compress(_DATA)
    path = tmp_path / f"blob{codec.suffix}"
    path.write_bytes(whole[: len(whole) // 2])
    with pytest.raises(ArtifactDecodeError) as info, codec.open_read(path) as fh:
        fh.read()
    assert isinstance(info.value.__cause__, codec.decode_errors)
    assert codec.name in str(info.value)


def test_zstd_writer_abandoned_short_of_its_pledge_leaves_no_complete_frame(tmp_path: Path) -> None:
    path = tmp_path / "blob.zst"
    with path.open("wb") as raw:
        with ZSTD.open_write(raw, source_size=len(_DATA)) as out:
            out.write(_DATA[:100])
    with pytest.raises(ArtifactDecodeError), ZSTD.open_read(path) as fh:
        fh.read()


def test_zstd_writer_refuses_to_exceed_its_pledge() -> None:
    raw = io.BytesIO()
    out = ZSTD.open_write(raw, source_size=10)
    with pytest.raises(OSError, match="pledged 10 bytes"):
        out.write(b"x" * 11)


def test_zstd_writer_without_a_pledge_records_no_size(tmp_path: Path) -> None:
    path = tmp_path / "blob.zst"
    with path.open("wb") as raw, ZSTD.open_write(raw) as out:
        out.write(_DATA)
    assert ZSTD.recorded_size(path) is None
    with ZSTD.open_read(path) as fh:
        assert fh.read() == _DATA


# ── Recorded size: what each container's figure means ────────────


def test_gzip_recorded_size_is_the_last_members_isize(tmp_path: Path) -> None:
    path = tmp_path / "blob.gz"
    path.write_bytes(gzip.compress(b"x" * 5000) + gzip.compress(b"y" * 10))
    assert GZIP.recorded_size(path) == 10  # not 5010: this figure is not a stream length
    with GZIP.open_read(path) as fh:
        assert len(fh.read()) == 5010


def test_gzip_recorded_size_reads_the_trailer_blindly(tmp_path: Path) -> None:
    bogus = tmp_path / "not-really.gz"
    bogus.write_bytes(b"\x00" * 8 + struct.pack("<I", 12345))
    assert GZIP.recorded_size(bogus) == 12345


def test_gzip_recorded_size_rejects_a_file_shorter_than_its_trailer(tmp_path: Path) -> None:
    tiny = tmp_path / "tiny.gz"
    tiny.write_bytes(b"\x00")
    with pytest.raises(ArtifactDecodeError):
        GZIP.recorded_size(tiny)


def test_zstd_recorded_size_is_the_first_frames_content_size(tmp_path: Path) -> None:
    path = tmp_path / "blob.zst"
    path.write_bytes(zstd.compress(b"x" * 5000) + zstd.compress(b"y" * 10))
    assert ZSTD.recorded_size(path) == 5000  # not 5010: this figure is not a stream length
    with ZSTD.open_read(path) as fh:
        assert len(fh.read()) == 5010


def test_zstd_recorded_size_is_unknown_behind_a_skippable_frame(tmp_path: Path) -> None:
    """A skippable frame records no content, which is unknown rather than empty.

    ``get_frame_info`` reports zero for one, which is true of that frame and false of
    the artifact; returning it would tell every consumer the stream is empty while
    ``open_read`` hands back the whole payload. The seekable-zstd and ``pzstd``
    formats both lead with one, so this is foreign input Metaproc can meet.
    """
    payload = b"z" * 4000
    skippable = struct.pack("<II", 0x184D2A50, 4) + b"\x00\x00\x00\x00"
    path = tmp_path / "blob.zst"
    path.write_bytes(skippable + zstd.compress(payload))

    assert ZSTD.recorded_size(path) is None
    with ZSTD.open_read(path) as fh:
        assert fh.read() == payload


def test_zstd_recorded_size_rejects_a_non_frame(tmp_path: Path) -> None:
    bogus = tmp_path / "not-really.zst"
    bogus.write_bytes(b"plain text, not a frame")
    with pytest.raises(ArtifactDecodeError):
        ZSTD.recorded_size(bogus)
