"""Transparent on-disk gzip support for browser artifact reads.

Provides :class:`ArtifactPath`, a thin wrapper that exposes both a
file's on-disk identity (``disk_path``, ``disk_size``) and its logical
identity (``logical_ext``, ``logical_size``, text/binary opens).
Browser endpoints construct one per request and key all classification,
sizing, and read behavior off it instead of sprinkling
``path.suffix == ".gz"`` checks across the codebase.

The split lets a ``foo.jsonl.gz`` produce byte-identical API envelopes
to ``foo.jsonl`` while the on-disk size and bytes-on-the-wire still
reflect the compressed reality.

Future archive formats (``.zst``, ``.br``, ``.xz``) plug in here, not at
every callsite.
"""

from __future__ import annotations

import gzip
import json
import mimetypes
import os
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, cast

# Single source of truth for the gzip on-disk suffix. Every callsite that
# detects, appends, or strips the gzip extension imports from here so a
# future archive format swap is a one-line change.
GZIP_SUFFIX: str = ".gz"
GZIP_PARTIAL_SUFFIX: str = GZIP_SUFFIX + ".partial"


def logical_path(path: Path) -> Path:
    """Return the uncompressed logical path for *path*.

    ``foo.jsonl.gz`` and ``foo.jsonl`` are the same logical stream. Visibility
    tooling uses this as the de-dupe key so compressed log compaction never
    changes which runs, steps, or attempts are visible.
    """
    if path.suffix.lower() == GZIP_SUFFIX:
        return path.with_suffix("")
    return path


def gzip_sibling(path: Path) -> Path:
    """Return the gzip-compressed sibling path for an uncompressed artifact."""
    return path.with_name(path.name + GZIP_SUFFIX)


def artifact_exists(path: Path) -> bool:
    """Return whether *path* exists, allowing for a ``.gz`` sibling."""
    return path.is_file() or gzip_sibling(path).is_file()


def resolve_existing_artifact(path: Path) -> Path:
    """Return *path* if present, else its ``.gz`` sibling if present.

    If neither exists, returns *path* unchanged. This preserves the existing
    path-construction contract while making read paths compression-aware.
    """
    if path.is_file():
        return path
    sibling = gzip_sibling(path)
    if sibling.is_file():
        return sibling
    return path


def artifact_sidecar_path(path: Path, sidecar_suffix: str) -> Path:
    """Return a sidecar path for the logical, uncompressed artifact name.

    Example: ``attempt.jsonl.gz`` with ``".jsonl.invocation.json"`` maps to
    ``attempt.jsonl.invocation.json``.
    """
    return logical_path(path).with_suffix(sidecar_suffix)


def iter_artifact_paths(root: Path, pattern: str) -> Iterable[Path]:
    """Yield files matching *pattern*, with transparent ``.gz`` support.

    ``pattern`` is passed to :meth:`Path.glob` and may include ``**``. Plain
    files are preferred when both ``foo.jsonl`` and ``foo.jsonl.gz`` exist.
    Returned paths are sorted by their logical uncompressed path for stable
    reports and tests.
    """
    if not root.exists():
        return

    by_logical: dict[Path, Path] = {}
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            by_logical[logical_path(path)] = path

    for path in sorted(root.glob(pattern + GZIP_SUFFIX)):
        if not path.is_file():
            continue
        _ = by_logical.setdefault(logical_path(path), path)

    for path in sorted(by_logical.values(), key=lambda p: str(logical_path(p))):
        yield path


def iter_text_lines(path: Path, *, errors: str = "replace") -> Iterable[tuple[int, str]]:
    """Yield ``(line_no, line)`` from plain or gzip-compressed text files."""
    try:
        with ArtifactPath(path).open_text(errors=errors) as fh:
            for line_no, line in enumerate(fh, start=1):
                yield line_no, line.rstrip("\n")
    except OSError:
        return


def iter_jsonl_records(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield JSON object lines from a plain or gzip-compressed JSONL stream."""
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
    """Yield JSON objects from a plain or gzip-compressed JSONL stream."""
    for _line_no, obj in iter_jsonl_records(path):
        yield obj


def _gz_uncompressed_size(path: Path) -> int:
    """Read the gzip ISIZE trailer (last four bytes) in O(1).

    ISIZE stores uncompressed size mod 2**32. We never produce or read
    files >= 2 GB (run artifacts cap well below that), so the modulo
    never bites in practice. Raises ``OSError`` on read failure;
    caller decides how to surface a malformed-trailer case.
    """
    with path.open("rb") as fh:
        _ = fh.seek(-4, os.SEEK_END)
        return struct.unpack("<I", fh.read(4))[0]


@dataclass(frozen=True)
class ArtifactPath:
    """A path the browser may open as either gzip-compressed or plain.

    Frozen: equality and hashing key off ``disk_path`` only. All derived
    properties are O(1) on top of cheap stat/suffix operations, so we
    don't bother caching them.
    """

    disk_path: Path

    @property
    def is_gzip(self) -> bool:
        return self.disk_path.suffix.lower() == GZIP_SUFFIX

    @property
    def logical_ext(self) -> str:
        """Inner extension: ``foo.jsonl.gz`` -> ``.jsonl``; ``foo.jsonl`` -> ``.jsonl``."""
        if self.is_gzip:
            return self.disk_path.with_suffix("").suffix.lower()
        return self.disk_path.suffix.lower()

    @property
    def logical_name(self) -> str:
        """Display filename with the ``.gz`` stripped when present."""
        if self.is_gzip:
            return self.disk_path.with_suffix("").name
        return self.disk_path.name

    @property
    def disk_size(self) -> int:
        return self.disk_path.stat().st_size

    @property
    def logical_size(self) -> int:
        """Uncompressed size. For ``.gz`` reads the ISIZE trailer."""
        if self.is_gzip:
            return _gz_uncompressed_size(self.disk_path)
        return self.disk_size

    @property
    def compression(self) -> str | None:
        """Compression scheme tag (e.g. ``"gzip"``). ``None`` for uncompressed."""
        return "gzip" if self.is_gzip else None

    @property
    def mime_type(self) -> str:
        """Best-guess MIME type derived from the *logical* filename."""
        mime, _ = mimetypes.guess_type(self.logical_name)
        return mime or "application/octet-stream"

    def open_text(self, errors: str = "replace") -> IO[str]:
        """Open as text. Always text mode; never returns a binary handle."""
        if self.is_gzip:
            return gzip.open(self.disk_path, "rt", errors=errors)
        return open(self.disk_path, errors=errors)

    def open_binary(self) -> IO[bytes]:
        """Open as binary, decompressing transparently when gzipped."""
        if self.is_gzip:
            # ``gzip.open(..., "rb")`` is typed as ``GzipFile`` rather than
            # the IO[bytes] protocol it actually implements at runtime.
            # Round-trip through ``object`` so basedpyright accepts the
            # cast; callers see the unified IO[bytes] type.
            return cast(IO[bytes], cast(object, gzip.open(self.disk_path, "rb")))
        return self.disk_path.open("rb")

    def passthrough_headers(self) -> dict[str, str]:
        """Headers for serving the on-disk gzip bytes verbatim to the client.

        Returns ``Content-Encoding: gzip`` + ``Vary: Accept-Encoding``
        when ``is_gzip``; empty dict otherwise. The caller is responsible
        for setting ``Content-Type`` (typically from :attr:`mime_type`).
        Pair with :meth:`open_binary` for the stream-decompress fallback
        when the client doesn't accept gzip.
        """
        if self.is_gzip:
            return {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
        return {}
