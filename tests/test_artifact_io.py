"""Tests for ``metaproc.io.artifact_io``.

Covers ``ArtifactPath`` (stored versus logical identity, both sizes under every codec,
text and binary opens, mime + headers), decode failures surfacing as one ``OSError``
subclass, and the logical-stream helpers that treat ``.jsonl``, ``.jsonl.gz``, and
``.jsonl.zst`` as one stream.
"""

from __future__ import annotations

import gzip
import json
from compression import zstd
from pathlib import Path

import pytest

from metaproc.io import ArtifactDecodeError
from metaproc.io.artifact_io import (
    ArtifactPath,
    artifact_exists,
    artifact_sidecar_path,
    compressed_sibling,
    iter_artifact_paths,
    iter_jsonl_records,
    iter_text_lines,
    logical_path,
    resolve_existing_artifact,
)
from metaproc.io.compression import CODECS, DEFAULT_WRITE_CODEC, GZIP, ZSTD, Codec

# Sample payload exercises bytes that round-trip cleanly through both codecs
# (UTF-8 with non-ASCII, repeated structure for a real ratio < 1).
_SAMPLE_TEXT = (
    '{"event": "init", "model": "claude-opus", "tokens": 42}\n'
    '{"event": "message", "text": "Hello — wörld"}\n' * 50
)
_SAMPLE_BYTES = _SAMPLE_TEXT.encode("utf-8")

_ENCODINGS = ("plain", *(codec.name for codec in CODECS))


def _encode(codec_name: str, data: bytes) -> bytes:
    """Encode *data* the way Metaproc's writer would: one member or frame, size recorded."""
    if codec_name == "plain":
        return data
    if codec_name == "gzip":
        return gzip.compress(data)
    if codec_name == "zstd":
        return zstd.compress(data)
    raise AssertionError(codec_name)


def _suffix(codec_name: str) -> str:
    return "" if codec_name == "plain" else next(c.suffix for c in CODECS if c.name == codec_name)


def _write_sample(tmp_path: Path, codec_name: str) -> Path:
    path = tmp_path / f"events.jsonl{_suffix(codec_name)}"
    path.write_bytes(_encode(codec_name, _SAMPLE_BYTES))
    return path


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(record) + "\n" for record in records)
    codec = ArtifactPath(path).codec
    path.write_bytes(text.encode("utf-8") if codec is None else _encode(codec.name, text.encode()))


# ── Identity and sizes ───────────────────────────────────────────


def test_plain_file_has_no_codec_and_equal_sizes(tmp_path: Path) -> None:
    a = ArtifactPath(_write_sample(tmp_path, "plain"))
    assert a.codec is None
    assert a.is_compressed is False
    assert a.compression is None
    assert a.logical_ext == ".jsonl"
    assert a.logical_name == "events.jsonl"
    assert a.disk_size == len(_SAMPLE_BYTES)
    assert a.logical_size == len(_SAMPLE_BYTES)
    assert a.passthrough_headers() == {}
    assert a.mime_type  # whatever mimetypes returns; just non-empty


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_compressed_file_reports_codec_and_both_sizes(tmp_path: Path, codec: Codec) -> None:
    path = _write_sample(tmp_path, codec.name)
    a = ArtifactPath(path)
    assert a.codec is codec
    assert a.is_compressed is True
    assert a.compression == codec.name
    assert a.logical_ext == ".jsonl"
    assert a.logical_name == "events.jsonl"
    assert a.logical_path == tmp_path / "events.jsonl"
    # Disk size is the stored size; smaller than the source.
    assert a.disk_size == path.stat().st_size < len(_SAMPLE_BYTES)
    # Logical size is the decoded size, whichever codec recorded it.
    assert a.logical_size == len(_SAMPLE_BYTES)
    assert a.passthrough_headers() == {
        "Content-Encoding": codec.content_encoding,
        "Vary": "Accept-Encoding",
    }


@pytest.mark.parametrize("codec_name", _ENCODINGS)
def test_open_text_and_binary_round_trip_every_encoding(tmp_path: Path, codec_name: str) -> None:
    a = ArtifactPath(_write_sample(tmp_path, codec_name))
    with a.open_text() as fh:
        assert fh.read() == _SAMPLE_TEXT
    with a.open_binary() as fh:
        assert fh.read() == _SAMPLE_BYTES


def test_logical_size_is_counted_when_the_container_records_none(tmp_path: Path) -> None:
    """A streaming zstd writer records no frame size; the answer is still exact."""
    path = tmp_path / "events.jsonl.zst"
    with zstd.open(path, "wb") as fh:
        fh.write(_SAMPLE_BYTES)
    assert ZSTD.recorded_size(path) is None
    assert ArtifactPath(path).logical_size == len(_SAMPLE_BYTES)


def test_artifactpath_is_hashable_and_equal_by_disk_path(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    p.write_bytes(b"x")
    assert ArtifactPath(p) == ArtifactPath(p)
    assert hash(ArtifactPath(p)) == hash(ArtifactPath(p))
    other = tmp_path / "g.jsonl"
    other.write_bytes(b"x")
    assert ArtifactPath(other) != ArtifactPath(p)


# ── Decode failures are one OSError ──────────────────────────────


def test_decode_error_is_an_oserror() -> None:
    assert issubclass(ArtifactDecodeError, OSError)


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_truncated_artifact_raises_decode_error_on_read(tmp_path: Path, codec: Codec) -> None:
    whole = _encode(codec.name, _SAMPLE_BYTES)
    path = tmp_path / f"events.jsonl{codec.suffix}"
    path.write_bytes(whole[: len(whole) // 2])
    with pytest.raises(ArtifactDecodeError), ArtifactPath(path).open_text() as fh:
        fh.read()
    with pytest.raises(OSError), ArtifactPath(path).open_binary() as fh:
        fh.read()


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_bytes_of_the_wrong_codec_raise_decode_error(tmp_path: Path, codec: Codec) -> None:
    path = tmp_path / f"events.jsonl{codec.suffix}"
    path.write_bytes(_SAMPLE_BYTES)  # plain bytes under a compressed suffix
    with pytest.raises(ArtifactDecodeError), ArtifactPath(path).open_text() as fh:
        fh.read()


def test_corrupt_gzip_trailer_raises_decode_error(tmp_path: Path) -> None:
    """Flip a CRC32 byte: decompression succeeds byte for byte, the trailer check rejects it."""
    path = _write_sample(tmp_path, "gzip")
    raw = bytearray(path.read_bytes())
    raw[-8] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises(ArtifactDecodeError), ArtifactPath(path).open_text() as fh:
        fh.read()


def test_iter_text_lines_ends_quietly_on_a_damaged_artifact(tmp_path: Path) -> None:
    whole = gzip.compress(_SAMPLE_BYTES)
    path = tmp_path / "events.jsonl.gz"
    path.write_bytes(whole[: len(whole) // 2])
    lines = list(iter_text_lines(path))  # must not raise
    assert len(lines) < _SAMPLE_TEXT.count("\n")


# ── Logical-stream identity ──────────────────────────────────────


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_logical_path_and_sidecar_strip_each_suffix(tmp_path: Path, codec: Codec) -> None:
    stored = tmp_path / f"attempt.jsonl{codec.suffix}"
    assert logical_path(stored) == tmp_path / "attempt.jsonl"
    assert artifact_sidecar_path(stored, ".jsonl.invocation.json") == (
        tmp_path / "attempt.jsonl.invocation.json"
    )
    assert compressed_sibling(tmp_path / "attempt.jsonl", codec) == stored


def test_logical_path_leaves_plain_and_partial_files_alone(tmp_path: Path) -> None:
    assert logical_path(tmp_path / "attempt.jsonl") == tmp_path / "attempt.jsonl"
    partial = tmp_path / "attempt.jsonl.gz.partial"
    assert logical_path(partial) == partial
    assert ArtifactPath(partial).codec is None


def test_compressed_sibling_defaults_to_the_write_codec(tmp_path: Path) -> None:
    assert compressed_sibling(tmp_path / "a.jsonl") == tmp_path / (
        "a.jsonl" + DEFAULT_WRITE_CODEC.suffix
    )


def test_resolution_prefers_plain_then_codecs_in_registry_order(tmp_path: Path) -> None:
    logical = tmp_path / "a.jsonl"
    assert not artifact_exists(logical)
    assert resolve_existing_artifact(logical) == logical  # unchanged when nothing exists

    _write_jsonl(logical.with_name("a.jsonl.zst"), [{"zstd": True}])
    assert artifact_exists(logical)
    assert resolve_existing_artifact(logical) == logical.with_name("a.jsonl.zst")

    _write_jsonl(logical.with_name("a.jsonl.gz"), [{"gzip": True}])
    assert resolve_existing_artifact(logical) == logical.with_name("a.jsonl.gz")
    assert [c.name for c in CODECS].index("gzip") < [c.name for c in CODECS].index("zstd")

    _write_jsonl(logical, [{"plain": True}])
    assert resolve_existing_artifact(logical) == logical


def test_partial_files_are_not_artifacts(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl.gz.partial").write_bytes(b"not published")
    assert not artifact_exists(tmp_path / "a.jsonl")
    assert list(iter_artifact_paths(tmp_path, "*.jsonl")) == []


def test_iter_artifact_paths_yields_one_file_per_logical_stream(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    _write_jsonl(logs / "a.jsonl", [{"plain": True}])
    _write_jsonl(logs / "a.jsonl.gz", [{"gzip_duplicate": True}])
    _write_jsonl(logs / "a.jsonl.zst", [{"zstd_duplicate": True}])
    _write_jsonl(logs / "b.jsonl.gz", [{"gzip": True}])
    _write_jsonl(logs / "b.jsonl.zst", [{"zstd_duplicate": True}])
    _write_jsonl(logs / "c.jsonl.zst", [{"zstd": True}])
    _write_jsonl(logs / "nested" / "d.jsonl.zst", [{"nested": True}])

    assert list(iter_artifact_paths(logs, "*.jsonl")) == [
        logs / "a.jsonl",
        logs / "b.jsonl.gz",
        logs / "c.jsonl.zst",
    ]
    assert list(iter_artifact_paths(logs, "**/*.jsonl")) == [
        logs / "a.jsonl",
        logs / "b.jsonl.gz",
        logs / "c.jsonl.zst",
        logs / "nested" / "d.jsonl.zst",
    ]
    assert list(iter_artifact_paths(tmp_path / "missing", "*.jsonl")) == []


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.name)
def test_jsonl_records_read_through_each_codec(tmp_path: Path, codec: Codec) -> None:
    path = tmp_path / f"attempt.jsonl{codec.suffix}"
    _write_jsonl(path, [{"type": "result"}, {"type": "turn.completed"}])
    assert list(iter_jsonl_records(path)) == [
        (1, {"type": "result"}),
        (2, {"type": "turn.completed"}),
    ]


def test_gzip_and_zstd_are_the_whole_registry() -> None:
    assert [c.name for c in CODECS] == ["gzip", "zstd"]
    assert GZIP in CODECS and ZSTD in CODECS
