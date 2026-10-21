"""Logical-stream identity for artifacts that may be stored compressed.

A logical artifact such as ``attempt.jsonl`` may exist on disk as ``attempt.jsonl``,
``attempt.jsonl.gz``, or ``attempt.jsonl.zst``, and transiently as a ``.partial`` its
writer has not published. :class:`ArtifactPath` separates the two identities a reader
needs: the stored one (``disk_path``, ``disk_size``, which codec) and the logical one
(``logical_path``, ``logical_ext``, ``logical_size``, text and binary opens of the
decoded bytes). The helpers around it answer whether a logical artifact exists, which
file holds it, and how to enumerate logical streams once each, so compaction never
changes which runs, steps, or attempts a tool can see.

The codec set lives in :mod:`metaproc.io.compression`; nothing here tests a suffix.
"""

from __future__ import annotations

import io
import json
import logging
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from metaproc.io.compression import (
    CODECS,
    DEFAULT_WRITE_CODEC,
    ArtifactDecodeError,
    Codec,
    codec_for_path,
)

log = logging.getLogger(__name__)

# Chunk for counting a decoded stream whose container records no size.
_COUNT_CHUNK = 1024 * 1024


def logical_path(path: Path) -> Path:
    """Return the logical, uncompressed path for *path*.

    ``foo.jsonl``, ``foo.jsonl.gz``, and ``foo.jsonl.zst`` are one logical stream.
    Visibility tooling uses this as the de-dupe key so compressed log compaction never
    changes which runs, steps, or attempts are visible.
    """
    if codec_for_path(path) is not None:
        return path.with_suffix("")
    return path


def compressed_sibling(path: Path, codec: Codec = DEFAULT_WRITE_CODEC) -> Path:
    """Return the path a compressed copy of the logical artifact *path* is stored at."""
    return path.with_name(path.name + codec.suffix)


def artifact_exists(path: Path) -> bool:
    """Return whether the logical artifact *path* exists in any supported encoding."""
    return resolve_existing_artifact(path).is_file()


def resolve_existing_artifact(path: Path) -> Path:
    """Return the file that holds the logical artifact *path*.

    The plain file wins, then compressed siblings in :data:`CODECS` order. When no
    encoding exists, *path* is returned unchanged, which preserves the existing
    path-construction contract while making read paths compression-aware.
    """
    if path.is_file():
        return path
    for codec in CODECS:
        sibling = compressed_sibling(path, codec)
        if sibling.is_file():
            return sibling
    return path


def artifact_sidecar_path(path: Path, sidecar_suffix: str) -> Path:
    """Return a sidecar path for the logical artifact name.

    Example: ``attempt.jsonl.gz`` with ``".jsonl.invocation.json"`` maps to
    ``attempt.jsonl.invocation.json``.
    """
    return logical_path(path).with_suffix(sidecar_suffix)


def iter_artifact_paths(root: Path, pattern: str) -> Iterable[Path]:
    """Yield the file holding each logical stream that matches *pattern* under *root*.

    ``pattern`` is passed to :meth:`Path.glob` and may include ``**``; it names the
    logical file, and every supported compressed suffix is matched as well. One path
    is yielded per logical stream: the plain file when it exists, else the compressed
    sibling earliest in :data:`CODECS` order. Results are sorted by logical path for
    stable reports and tests.
    """
    if not root.exists():
        return

    by_logical: dict[Path, Path] = {}
    for suffix in ("", *(codec.suffix for codec in CODECS)):
        for path in sorted(root.glob(pattern + suffix)):
            if path.is_file():
                _ = by_logical.setdefault(logical_path(path), path)

    for logical in sorted(by_logical):
        yield by_logical[logical]


def iter_text_lines(path: Path, *, errors: str = "replace") -> Iterable[tuple[int, str]]:
    """Yield ``(line_no, line)`` from a plain or compressed text file.

    A file that cannot be opened ends the iteration quietly, as every reader of
    best-effort operational streams expects. A file that opens but cannot be decoded
    is damage rather than a short stream, and every caller here reports on what it
    read, so it warns before ending: without that, a truncated artifact is
    indistinguishable from a step that simply logged less.
    """
    try:
        with ArtifactPath(path).open_text(errors=errors) as fh:
            for line_no, line in enumerate(fh, start=1):
                yield line_no, line.rstrip("\n")
    except ArtifactDecodeError as exc:
        log.warning("Stopped reading %s at undecodable data: %s", logical_path(path), exc)
        return
    except OSError:
        return


def iter_jsonl_records(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield JSON object lines from a plain or compressed JSONL stream."""
    for line_no, line in iter_text_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield line_no, obj


def iter_jsonl_objects(path: Path) -> Iterable[dict[str, Any]]:
    """Yield JSON objects from a plain or compressed JSONL stream."""
    for _line_no, obj in iter_jsonl_records(path):
        yield obj


@dataclass(frozen=True)
class ArtifactPath:
    """A stored file and the logical artifact it holds, plain or compressed.

    Frozen: equality and hashing key off ``disk_path`` only. Every property is O(1) on
    top of a suffix test or one ``stat``, so nothing is cached. No property or method
    asks a caller which codec is in use; the codec is consulted here, once, through
    :attr:`codec`.
    """

    disk_path: Path

    @property
    def codec(self) -> Codec | None:
        """The codec the stored file is encoded with, or ``None`` for a plain file."""
        return codec_for_path(self.disk_path)

    @property
    def is_compressed(self) -> bool:
        return self.codec is not None

    @property
    def compression(self) -> str | None:
        """Codec name tag (``"gzip"``, ``"zstd"``); ``None`` for a plain file."""
        codec = self.codec
        return None if codec is None else codec.name

    @property
    def logical_path(self) -> Path:
        return logical_path(self.disk_path)

    @property
    def logical_ext(self) -> str:
        """Inner extension: ``foo.jsonl.zst`` -> ``.jsonl``; ``foo.jsonl`` -> ``.jsonl``."""
        return self.logical_path.suffix.lower()

    @property
    def logical_name(self) -> str:
        """Display filename with any compression suffix stripped."""
        return self.logical_path.name

    @property
    def disk_size(self) -> int:
        """Bytes the file occupies as stored: what ``du`` counts and a passthrough serves."""
        return self.disk_path.stat().st_size

    @property
    def logical_size(self) -> int:
        """Bytes of the decoded stream: what :meth:`open_binary` yields end to end.

        Equal to :attr:`disk_size` for a plain file. For a compressed one it is the size
        the container records (:meth:`Codec.recorded_size`), read without decoding, and
        the stream is decoded and counted when the container records none. Exact for
        artifacts Metaproc writes. It serves display, thresholds, and cache keys; a
        reader tracking an offset into the decoded stream must not compare against it.
        """
        codec = self.codec
        if codec is None:
            return self.disk_size
        recorded = codec.recorded_size(self.disk_path)
        if recorded is not None:
            return recorded
        total = 0
        with self.open_binary() as fh:
            while chunk := fh.read(_COUNT_CHUNK):
                total += len(chunk)
        return total

    @property
    def mime_type(self) -> str:
        """Best-guess MIME type derived from the *logical* filename."""
        mime, _ = mimetypes.guess_type(self.logical_name)
        return mime or "application/octet-stream"

    def open_text(self, errors: str = "replace") -> IO[str]:
        """Open the decoded stream as UTF-8 text. Always text mode; never a binary handle."""
        codec = self.codec
        if codec is None:
            return open(self.disk_path, encoding="utf-8", errors=errors)
        return io.TextIOWrapper(codec.open_read(self.disk_path), encoding="utf-8", errors=errors)

    def open_binary(self) -> IO[bytes]:
        """Open the decoded stream as bytes, decompressing transparently."""
        codec = self.codec
        if codec is None:
            return self.disk_path.open("rb")
        return codec.open_read(self.disk_path)

    def passthrough_headers(self) -> dict[str, str]:
        """Headers for serving the stored bytes verbatim to a client that accepts them.

        ``Content-Encoding`` names the codec plus ``Vary: Accept-Encoding`` when
        compressed; an empty dict otherwise. The caller sets ``Content-Type`` (typically
        from :attr:`mime_type`) and pairs this with :meth:`open_binary` as the
        decode-on-the-fly fallback when the client does not accept the encoding.
        """
        codec = self.codec
        if codec is None:
            return {}
        return {"Content-Encoding": codec.content_encoding, "Vary": "Accept-Encoding"}
