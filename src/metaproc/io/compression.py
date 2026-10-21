"""Compression codecs for on-disk artifacts: the closed set, one object per codec.

Metaproc stores large completed artifacts compressed. Two codecs are supported, gzip
(``.gz``) and Zstandard (``.zst``), and everything specific to one of them hangs off its
:class:`Codec`: the on-disk suffix, the decoding reader, the encoding writer, the decoded
size its container records, the HTTP ``Content-Encoding`` token, and the backend
exceptions it translates into :class:`ArtifactDecodeError`. Nothing outside this module
tests a suffix or imports a compression backend.

Reading is symmetric: every registered codec is always readable, so a run directory
holding ``.gz`` and ``.zst`` artifacts side by side is ordinary. Writing has one default,
:data:`DEFAULT_WRITE_CODEC`; moving Metaproc's own writers to another codec is that one
assignment. :mod:`metaproc.io.artifact_io` builds logical-stream identity, the same
stream stored plain or under either suffix, on top of this registry.

Zstandard comes from the standard library's :mod:`compression.zstd`, present from
Python 3.14, which is this package's floor.
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import zlib
from abc import ABC, abstractmethod
from collections.abc import Buffer
from compression import zstd
from pathlib import Path
from typing import IO, ClassVar, cast

# A Zstandard frame header is 6 to 18 bytes; reading the maximum covers every frame.
_ZSTD_FRAME_HEADER_MAX = 18
# Skippable frames use magic 0x184D2A50 through 0x184D2A5F and carry no content size.
_ZSTD_SKIPPABLE_MAGIC_MIN = 0x184D2A50
_ZSTD_SKIPPABLE_MAGIC_MAX = 0x184D2A5F


class ArtifactDecodeError(OSError):
    """A compressed artifact could not be decoded.

    Raised for a truncated or corrupt stream and for bytes that are not the codec the
    suffix claims. It is an ``OSError`` on purpose: a reader's ``except OSError`` treats
    an artifact it cannot decode exactly like one it cannot open, whichever codec wrote
    it, and the backend's own exception (``EOFError``, ``zlib.error``, ``ZstdError``)
    never reaches a caller.
    """


class Codec(ABC):
    """One compression codec; every codec-specific fact a caller needs lives here."""

    name: ClassVar[str]
    """Short tag, ``"gzip"`` or ``"zstd"``; also the ``compression`` value in API envelopes."""

    suffix: ClassVar[str]
    """On-disk suffix appended to the logical name, with its dot."""

    content_encoding: ClassVar[str]
    """HTTP ``Content-Encoding`` token for serving the stored bytes verbatim."""

    default_level: ClassVar[int]
    level_range: ClassVar[tuple[int, int]]

    decode_errors: ClassVar[tuple[type[BaseException], ...]]
    """Backend exceptions a read can raise; :meth:`open_read` translates them."""

    @property
    def partial_suffix(self) -> str:
        """Suffix of a file still being written, which no reader treats as an artifact."""
        return self.suffix + ".partial"

    def open_read(self, path: Path) -> IO[bytes]:
        """Open *path* for decoded reads.

        Backend failures surface as :class:`ArtifactDecodeError`; ``fileno()`` is the
        stored file's, so ``os.fstat`` on the handle describes the artifact being read.
        """
        stream = _DecodedStream(self._open_backend(path), self, path)
        # ``BufferedIOBase`` implements the ``IO[bytes]`` protocol at runtime but is not
        # nominally related to it; round-trip through ``object`` so the cast is accepted.
        return cast(IO[bytes], cast(object, stream))

    def check_level(self, level: int | None) -> int:
        """Return *level*, or the codec default for ``None``; reject one outside the range."""
        if level is None:
            return self.default_level
        low, high = self.level_range
        if not low <= level <= high:
            raise ValueError(f"{self.name} level must be between {low} and {high}, got {level}")
        return level

    @abstractmethod
    def _open_backend(self, path: Path) -> io.BufferedIOBase:
        """Open the backend's own decoding handle; its errors are translated by the caller."""

    @abstractmethod
    def open_write(
        self,
        raw: IO[bytes],
        *,
        level: int | None = None,
        source_size: int | None = None,
        source_mtime: float | None = None,
    ) -> IO[bytes]:
        """Wrap *raw* so bytes written to the result land in *raw* encoded.

        ``source_size`` lets the codec record the decoded size in its container when the
        total is known up front, so :meth:`recorded_size` can answer without decoding;
        when given, exactly that many bytes must be written before the handle is
        closed. ``source_mtime`` is stored where the container has a slot for it.
        Closing the returned handle finalizes the container but does not close *raw*.
        """

    @abstractmethod
    def compress(self, data: bytes, *, level: int | None = None) -> bytes:
        """One-shot encode of a buffer, for size estimates and tests."""

    @abstractmethod
    def recorded_size(self, path: Path) -> int | None:
        """The decoded size the container at *path* records, or ``None`` if it records none.

        An O(1) read of the container's own figure, never a decode. Each codec documents
        what its figure describes; both describe one member or frame, which is all
        Metaproc's writers produce. It serves display, thresholds, and cache keys. A
        reader tracking its position in a decoded stream must not compare an offset
        against it: a file holding several members or frames is complete at a size this
        figure does not describe.
        """


class _DecodedStream(io.BufferedIOBase):
    """A codec backend's decoded stream, with the backend's failures translated.

    Delegates to the backend handle and raises :class:`ArtifactDecodeError` wherever the
    backend would raise one of its :attr:`Codec.decode_errors`.
    """

    def __init__(self, inner: io.BufferedIOBase, codec: Codec, path: Path) -> None:
        super().__init__()
        self._inner = inner
        self._codec = codec
        self._path = path

    def _decode_error(self, exc: BaseException) -> ArtifactDecodeError:
        return ArtifactDecodeError(f"cannot decode {self._codec.name} artifact {self._path}: {exc}")

    @property
    def name(self) -> str:
        return str(self._path)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return self._inner.seekable()

    def fileno(self) -> int:
        return self._inner.fileno()

    def read(self, size: int | None = -1, /) -> bytes:
        try:
            return self._inner.read(-1 if size is None else size)
        except self._codec.decode_errors as exc:
            raise self._decode_error(exc) from exc

    def read1(self, size: int = -1, /) -> bytes:
        try:
            return self._inner.read1(size)
        except self._codec.decode_errors as exc:
            raise self._decode_error(exc) from exc

    def readinto(self, buffer: Buffer, /) -> int:
        try:
            return self._inner.readinto(buffer)
        except self._codec.decode_errors as exc:
            raise self._decode_error(exc) from exc

    def readline(self, size: int | None = -1, /) -> bytes:
        try:
            return self._inner.readline(-1 if size is None else size)
        except self._codec.decode_errors as exc:
            raise self._decode_error(exc) from exc

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        try:
            return self._inner.seek(offset, whence)
        except self._codec.decode_errors as exc:
            raise self._decode_error(exc) from exc

    def tell(self) -> int:
        return self._inner.tell()

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._inner.close()
        finally:
            super().close()


class _GzipCodec(Codec):
    """gzip: deflate members per RFC 1952, read and written with :mod:`gzip`."""

    name = "gzip"
    suffix = ".gz"
    content_encoding = "gzip"
    default_level = 6
    level_range = (1, 9)
    decode_errors = (gzip.BadGzipFile, EOFError, zlib.error)

    def _open_backend(self, path: Path) -> io.BufferedIOBase:
        return gzip.GzipFile(path, "rb")

    def open_write(
        self,
        raw: IO[bytes],
        *,
        level: int | None = None,
        source_size: int | None = None,
        source_mtime: float | None = None,
    ) -> IO[bytes]:
        writer = gzip.GzipFile(
            fileobj=raw,
            mode="wb",
            compresslevel=self.check_level(level),
            mtime=None if source_mtime is None else int(source_mtime),
        )
        return cast(IO[bytes], cast(object, writer))

    def compress(self, data: bytes, *, level: int | None = None) -> bytes:
        return gzip.compress(data, compresslevel=self.check_level(level))

    def recorded_size(self, path: Path) -> int | None:
        """ISIZE, the last four bytes: the final member's decoded size modulo 2**32.

        Read blindly; a malformed file at least four bytes long yields whatever its last
        four bytes say. A file shorter than its trailer is an :class:`ArtifactDecodeError`.
        """
        with path.open("rb") as fh:
            if fh.seek(0, os.SEEK_END) < 4:
                raise ArtifactDecodeError(f"cannot decode gzip artifact {path}: no trailer")
            fh.seek(-4, os.SEEK_END)
            return struct.unpack("<I", fh.read(4))[0]


class _ZstdFrameWriter(io.BufferedIOBase):
    """Encode writes into one Zstandard frame on *raw*, pledging the content size when known.

    A pledge puts the decoded size in the frame header, where
    :meth:`Codec.recorded_size` reads it without decoding. The frame is finalized on
    close only when the pledge was met; a writer abandoned short of its pledge leaves
    an unfinished frame, which no reader accepts, rather than a complete frame that
    lies about its size.
    """

    def __init__(self, raw: IO[bytes], *, level: int, pledged_size: int | None) -> None:
        super().__init__()
        self._raw = raw
        self._compressor = zstd.ZstdCompressor(level=level)
        self._pledged = pledged_size
        self._written = 0
        if pledged_size is not None:
            self._compressor.set_pledged_input_size(pledged_size)

    def writable(self) -> bool:
        return True

    def write(self, data: Buffer, /) -> int:
        view = memoryview(data)
        if self._pledged is not None and self._written + view.nbytes > self._pledged:
            raise OSError(
                f"zstd frame pledged {self._pledged} bytes; "
                f"writing {view.nbytes} more after {self._written} would exceed it"
            )
        encoded = self._compressor.compress(view)
        if encoded:
            self._raw.write(encoded)
        self._written += view.nbytes
        return view.nbytes

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self._pledged is None or self._written == self._pledged:
                self._raw.write(self._compressor.flush())
        finally:
            super().close()


class _ZstdCodec(Codec):
    """Zstandard frames per RFC 8878, read and written with :mod:`compression.zstd`."""

    name = "zstd"
    suffix = ".zst"
    content_encoding = "zstd"
    default_level = zstd.COMPRESSION_LEVEL_DEFAULT
    level_range = (1, 22)
    decode_errors = (zstd.ZstdError, EOFError)

    def _open_backend(self, path: Path) -> io.BufferedIOBase:
        return zstd.ZstdFile(path, "rb")

    def open_write(
        self,
        raw: IO[bytes],
        *,
        level: int | None = None,
        source_size: int | None = None,
        source_mtime: float | None = None,
    ) -> IO[bytes]:
        writer = _ZstdFrameWriter(raw, level=self.check_level(level), pledged_size=source_size)
        return cast(IO[bytes], cast(object, writer))

    def compress(self, data: bytes, *, level: int | None = None) -> bytes:
        return zstd.compress(data, level=self.check_level(level))

    def recorded_size(self, path: Path) -> int | None:
        """The first frame's content size, present only when its writer pledged it.

        Metaproc's writer pledges it; :func:`compression.zstd.compress` records it; a
        streaming writer such as ``ZstdFile`` or the ``zstd`` CLI does not, and the
        answer is then ``None``.

        A foreign file may lead with a skippable frame, as the seekable-zstd and
        ``pzstd`` formats do. ``get_frame_info`` reports zero content for one, which is
        a true statement about that frame and a false one about the artifact, so the
        answer is ``None``: unknown rather than empty.
        """
        with path.open("rb") as fh:
            header = fh.read(_ZSTD_FRAME_HEADER_MAX)
        if len(header) >= 4:
            magic = int.from_bytes(header[:4], "little")
            if _ZSTD_SKIPPABLE_MAGIC_MIN <= magic <= _ZSTD_SKIPPABLE_MAGIC_MAX:
                return None
        try:
            return zstd.get_frame_info(header).decompressed_size
        except zstd.ZstdError as exc:
            raise ArtifactDecodeError(f"cannot decode zstd artifact {path}: {exc}") from exc


GZIP: Codec = _GzipCodec()
ZSTD: Codec = _ZstdCodec()

CODECS: tuple[Codec, ...] = (GZIP, ZSTD)
"""Every codec Metaproc reads, in the order that breaks ties between encodings of one stream."""

DEFAULT_WRITE_CODEC: Codec = GZIP
"""What Metaproc's own writers produce.

Zstandard is the intended successor. The switch is this one assignment; what gates it is
recorded in the artifact compression design record under ``docs/project/design/``.
"""

COMPRESSION_SUFFIXES: frozenset[str] = frozenset(codec.suffix for codec in CODECS)

_BY_SUFFIX: dict[str, Codec] = {codec.suffix: codec for codec in CODECS}
_BY_NAME: dict[str, Codec] = {codec.name: codec for codec in CODECS}


def codec_for_path(path: Path) -> Codec | None:
    """The codec *path*'s suffix names, or ``None`` for a plain file. Case-insensitive."""
    return _BY_SUFFIX.get(path.suffix.lower())


def codec_by_name(name: str) -> Codec:
    """The codec called *name*; a ``ValueError`` names the valid choices otherwise."""
    try:
        return _BY_NAME[name.lower()]
    except KeyError:
        choices = ", ".join(sorted(_BY_NAME))
        raise ValueError(f"unknown compression codec {name!r}; choose one of: {choices}") from None
