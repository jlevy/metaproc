"""Tests for ``metaproc.io.gz_io``.

Covers the ``ArtifactPath`` abstraction (logical vs disk identity, text
and binary opens, mime + headers) plus the ``_gz_uncompressed_size``
helper, including malformed/truncated inputs that browser callers will
encounter on a corrupted run dir. Also covers shared visibility helpers
that treat ``.jsonl`` and ``.jsonl.gz`` as the same logical stream.
"""

from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

import pytest

from metaproc.io.gz_io import (
    ArtifactPath,
    _gz_uncompressed_size,
    artifact_sidecar_path,
    iter_artifact_paths,
    iter_jsonl_records,
    resolve_existing_artifact,
)

# Sample payload exercises bytes that round-trip cleanly through gzip
# (UTF-8 with non-ASCII, repeated structure for a real ratio < 1).
_SAMPLE_TEXT = (
    '{"event": "init", "model": "claude-opus", "tokens": 42}\n'
    '{"event": "message", "text": "Hello — wörld"}\n' * 50
)
_SAMPLE_BYTES = _SAMPLE_TEXT.encode("utf-8")


def _make_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write ``events.jsonl`` + ``events.jsonl.gz`` with identical content."""
    plain = tmp_path / "events.jsonl"
    plain.write_bytes(_SAMPLE_BYTES)
    gz = tmp_path / "events.jsonl.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(_SAMPLE_BYTES)
    return plain, gz


def _write_gzip_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def test_artifactpath_plain_file_identifies_correctly(tmp_path: Path) -> None:
    plain, _ = _make_pair(tmp_path)
    a = ArtifactPath(plain)
    assert a.is_gzip is False
    assert a.logical_ext == ".jsonl"
    assert a.logical_name == "events.jsonl"
    assert a.compression is None
    assert a.disk_size == len(_SAMPLE_BYTES)
    assert a.logical_size == len(_SAMPLE_BYTES)
    assert a.passthrough_headers() == {}
    assert a.mime_type  # whatever mimetypes returns; just non-empty


def test_artifactpath_gzip_file_strips_gz_suffix(tmp_path: Path) -> None:
    _, gz = _make_pair(tmp_path)
    a = ArtifactPath(gz)
    assert a.is_gzip is True
    assert a.logical_ext == ".jsonl"
    assert a.logical_name == "events.jsonl"
    assert a.compression == "gzip"
    # Disk size is the compressed size; smaller than the source.
    assert a.disk_size < len(_SAMPLE_BYTES)
    # Logical size is the uncompressed size.
    assert a.logical_size == len(_SAMPLE_BYTES)
    assert a.passthrough_headers() == {
        "Content-Encoding": "gzip",
        "Vary": "Accept-Encoding",
    }


def test_artifactpath_open_text_round_trips_gzip_and_plain(tmp_path: Path) -> None:
    plain, gz = _make_pair(tmp_path)
    with ArtifactPath(plain).open_text() as fh:
        plain_text = fh.read()
    with ArtifactPath(gz).open_text() as fh:
        gz_text = fh.read()
    assert plain_text == _SAMPLE_TEXT
    assert gz_text == _SAMPLE_TEXT


def test_artifactpath_open_binary_round_trips_gzip_and_plain(tmp_path: Path) -> None:
    plain, gz = _make_pair(tmp_path)
    with ArtifactPath(plain).open_binary() as fh:
        plain_bytes = fh.read()
    with ArtifactPath(gz).open_binary() as fh:
        gz_bytes = fh.read()
    assert plain_bytes == _SAMPLE_BYTES
    assert gz_bytes == _SAMPLE_BYTES


def test_artifactpath_is_hashable_and_equal_by_disk_path(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    p.write_bytes(b"x")
    a1 = ArtifactPath(p)
    a2 = ArtifactPath(p)
    assert a1 == a2
    assert hash(a1) == hash(a2)
    # Sanity: different path -> different hash, different equality.
    other = tmp_path / "g.jsonl"
    other.write_bytes(b"x")
    assert ArtifactPath(other) != a1


def test_gz_uncompressed_size_matches_real_size(tmp_path: Path) -> None:
    _, gz = _make_pair(tmp_path)
    assert _gz_uncompressed_size(gz) == len(_SAMPLE_BYTES)


def test_open_text_propagates_bad_gzip_on_truncated_file(tmp_path: Path) -> None:
    """Truncated mid-stream: gzip surfaces a clean error during read."""
    _, gz = _make_pair(tmp_path)
    truncated = gz.read_bytes()[: len(gz.read_bytes()) // 2]
    bad = tmp_path / "events.jsonl.gz"
    bad.write_bytes(truncated)

    with pytest.raises((gzip.BadGzipFile, EOFError, OSError)):
        with ArtifactPath(bad).open_text() as fh:
            fh.read()


def test_open_text_propagates_crc_mismatch(tmp_path: Path) -> None:
    """Tamper with the gzip trailer's CRC32; close() fails on read-to-EOF.

    The last 8 bytes of a gzip member are CRC32 + ISIZE. Flipping a byte
    inside the CRC32 makes decompression succeed byte-for-byte but the
    trailer check rejects the payload. This exercises the same code
    path that catches mid-stream corruption, with deterministic
    triggering (a deflate-stream byte flip might happen to produce
    valid-looking bytes whose CRC matches).
    """
    _, gz = _make_pair(tmp_path)
    raw = bytearray(gz.read_bytes())
    raw[-8] ^= 0xFF  # corrupt CRC32 byte 0
    gz.write_bytes(bytes(raw))

    with pytest.raises((gzip.BadGzipFile, EOFError, OSError)):
        with ArtifactPath(gz).open_text() as fh:
            fh.read()


def test_gz_uncompressed_size_on_garbage_returns_garbage(tmp_path: Path) -> None:
    """``_gz_uncompressed_size`` reads ISIZE blindly. A malformed file
    that happens to be >= 4 bytes long won't raise — it returns whatever
    the last four bytes interpret as. Document and pin this; callers
    that need stronger validation should pair with an actual decompress."""
    bogus = tmp_path / "not-really.gz"
    bogus.write_bytes(b"\x00" * 8 + struct.pack("<I", 12345))
    assert _gz_uncompressed_size(bogus) == 12345


def test_gz_uncompressed_size_raises_on_too_small_file(tmp_path: Path) -> None:
    """A file shorter than four bytes can't carry an ISIZE trailer."""
    tiny = tmp_path / "tiny.gz"
    tiny.write_bytes(b"\x00")
    with pytest.raises(OSError):
        _gz_uncompressed_size(tiny)


def test_iter_artifact_paths_reads_plain_and_gzip_with_logical_dedupe(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    plain = logs / "a.jsonl"
    plain.write_text('{"plain": true}\n', encoding="utf-8")
    _write_gzip_jsonl(logs / "a.jsonl.gz", [{"compressed_duplicate": True}])
    _write_gzip_jsonl(logs / "b.jsonl.gz", [{"compressed": True}])

    assert list(iter_artifact_paths(logs, "*.jsonl")) == [
        plain,
        logs / "b.jsonl.gz",
    ]


def test_jsonl_records_and_sidecars_use_logical_paths(tmp_path: Path) -> None:
    gz_path = tmp_path / "attempt.jsonl.gz"
    _write_gzip_jsonl(gz_path, [{"type": "result"}, {"type": "turn.completed"}])

    assert resolve_existing_artifact(tmp_path / "attempt.jsonl") == gz_path
    assert artifact_sidecar_path(gz_path, ".jsonl.invocation.json") == (
        tmp_path / "attempt.jsonl.invocation.json"
    )
    assert list(iter_jsonl_records(gz_path)) == [
        (1, {"type": "result"}),
        (2, {"type": "turn.completed"}),
    ]
