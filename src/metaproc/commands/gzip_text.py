"""metaproc gzip-text — idempotently compress text-format files to reclaim disk.

Walks a path and compresses every matching file to ``<file><suffix>`` with one
codec from ``metaproc.io.compression`` (gzip by default; ``--codec zstd`` selects
Zstandard), verifies the round-trip decodes to the original bytes, then deletes
the original. Skips files that already have a compressed sibling in any codec,
so re-runs are a no-op and one logical stream never exists in two encodings.

Default extension set + per-extension minimum-size thresholds live in
``metaproc.osutils.file_extensions.GZIP_DEFAULT_THRESHOLDS``. Tuned
per artifact shape: log streams (``.jsonl``, ``.log``) get the lower
128 KiB floor; structured documents (``.json``, ``.yaml``, ``.yml``)
get the higher 1 MiB floor so small tracked artifacts stay raw and
keep their GitHub diff/blame/code-search story intact. The floors
decide whether a file is worth compressing at all and so are the same
for every codec. Operators override per-call via ``--include``
(extension subset) and ``--min-size`` (single global floor that
overrides per-extension).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer
from prettyfmt import fmt_size_human

from metaproc.cli import app, get_output
from metaproc.io.artifact_io import compressed_sibling
from metaproc.io.compression import (
    CODECS,
    DEFAULT_WRITE_CODEC,
    Codec,
    codec_by_name,
    codec_for_path,
)
from metaproc.osutils.file_extensions import (
    NEVER_GZIP,
    gzip_default_include_csv,
    gzip_threshold_for,
)

# Source of truth for the default extension set + per-extension minimum
# sizes lives in ``metaproc.osutils.file_extensions.GZIP_DEFAULT_THRESHOLDS``.
# The CLI binds the extension list as a string here for typer's
# default-display formatting; per-extension thresholds are looked up at
# classify time via ``gzip_threshold_for``.
_DEFAULT_INCLUDE = gzip_default_include_csv()

# Floor for extensions that the operator opts into via ``--include`` but
# that the defaults table doesn't know about. Sized to match the
# structured-document tier (1 MiB) so a one-off ``--include .csv`` sweep
# behaves like ``.json`` / ``.yaml`` rather than compressing anything down
# to a few KB.
_FALLBACK_MIN_SIZE = 1024 * 1024

# Streaming chunk for both compress and verify passes.
_STREAM_CHUNK = 256 * 1024

# A file modified within this window is treated as "still being written
# to". Skip it so we never race a live producer.
_ACTIVE_WINDOW_SECONDS = 60.0


class SourceChangedDuringCompression(RuntimeError):
    """Raised by ``compress_file`` when the source changed under the sweep.

    Either its ``(size, mtime_ns)`` fingerprint changed between the pre-compress
    snapshot and the pre-publish re-stat, or it shrank while being read. Distinct
    from generic ``RuntimeError`` so the CLI can map it to a soft skip rather than
    a hard failure: a producer that woke up mid-sweep is a normal racing
    condition, not a bug.
    """


@dataclass(frozen=True)
class GzCandidate:
    path: Path
    size: int
    skip_reason: str | None  # None = eligible


@dataclass(frozen=True)
class GzResult:
    path: Path
    codec: str
    original_size: int
    compressed_size: int

    @property
    def saved_bytes(self) -> int:
        return self.original_size - self.compressed_size

    @property
    def reduction_pct(self) -> float:
        if self.original_size == 0:
            return 0.0
        return 100.0 * self.saved_bytes / self.original_size


@dataclass(frozen=True)
class GzipTextSummary:
    """Programmatic summary for a gzip-text sweep."""

    codec: str
    matched: int
    eligible: int
    skipped: int
    compressed: int
    failed: int
    raced: int
    original_bytes: int
    compressed_bytes: int
    saved_bytes: int


def _parse_exts(spec: str) -> frozenset[str]:
    """Parse ``'.yaml,.md,txt'`` into ``frozenset({'.yaml', '.md', '.txt'})``.

    Accepts entries with or without a leading dot; normalizes to lowercase.
    """
    out: set[str] = set()
    for raw in spec.split(","):
        s = raw.strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        out.add(s)
    return frozenset(out)


def _walk_files(root: Path) -> list[Path]:
    """Return every regular file under root (or [root] if root is itself a file).

    Skips symlinks so we never escape the tree the user pointed at.
    """
    if root.is_file() and not root.is_symlink():
        return [root]
    if not root.is_dir():
        return []
    out: list[Path] = []
    for dirpath, _, filenames in os.walk(root, followlinks=False):
        for fname in filenames:
            p = Path(dirpath) / fname
            if not p.is_symlink():
                out.append(p)
    return out


def _classify_candidates(
    files: list[Path],
    *,
    thresholds: dict[str, int],
    exclude_active: bool,
    active_window_s: float,
) -> list[GzCandidate]:
    """Decide for each file whether to compress, and why if not.

    ``thresholds`` is keyed by lowercased extension and gives the minimum
    file size in bytes for that extension to qualify; absence means the
    extension is not in scope for this run; ``NEVER_GZIP`` (-1) is an
    explicit denylist entry. A file that already has a compressed sibling
    in any codec is skipped, whichever codec this sweep writes: one logical
    stream is stored in one encoding.
    """
    now = time.time()
    out: list[GzCandidate] = []
    for p in files:
        if codec_for_path(p) is not None:
            continue  # Already compressed — never re-touch.
        suffix = p.suffix.lower()
        threshold = thresholds.get(suffix)
        if threshold is None or threshold == NEVER_GZIP:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        size = st.st_size
        if any(compressed_sibling(p, codec).exists() for codec in CODECS):
            out.append(GzCandidate(p, size, "already-compressed"))
            continue
        if size < threshold:
            out.append(GzCandidate(p, size, "below-min-size"))
            continue
        if exclude_active and (now - st.st_mtime) < active_window_s:
            out.append(GzCandidate(p, size, "recently-modified"))
            continue
        out.append(GzCandidate(p, size, None))
    return out


def _build_thresholds(exts: frozenset[str], *, min_size_override: int | None) -> dict[str, int]:
    """Per-extension min-size for ``exts``.

    When ``min_size_override`` is given (operator passed ``--min-size``),
    every extension uses that single value, defaults are bypassed, and
    even ``NEVER_GZIP`` entries respect the override (the operator is
    asking for an ad-hoc sweep).

    Otherwise look up each extension in ``GZIP_DEFAULT_THRESHOLDS``,
    falling back to ``_FALLBACK_MIN_SIZE`` for opted-in extensions that
    the defaults table doesn't know about.
    """
    if min_size_override is not None:
        return {ext: min_size_override for ext in exts}
    return {ext: gzip_threshold_for(ext, fallback=_FALLBACK_MIN_SIZE) for ext in exts}


def compress_file(
    src: Path,
    *,
    codec: Codec = DEFAULT_WRITE_CODEC,
    level: int | None = None,
    keep_original: bool = False,
) -> GzResult:
    """Compress ``src`` to its ``codec`` sibling atomically with byte-fidelity proof.

    Steps:
      1. Snapshot ``(size, mtime_ns)`` of source.
      2. Stream-compress exactly the snapshot's byte count to the codec's
         ``.partial`` staging file, hashing source bytes with SHA-256 as
         we read them. The codec records the decoded size in its
         container when it can, so later size probes need no decode.
      3. fsync; close.
      4. Decode the partial back through the codec, hashing decoded
         bytes. The codec verifies its own integrity data on the way.
      5. **Byte-fidelity check**: assert decoded digest == source digest
         *and* decoded length == original length. A container checksum
         plus length alone don't prove the decoded bytes equal the
         source bytes (a hardware-level corruption between source read
         and encoder write would compress and decompress cleanly).
      6. **Stability check**: re-stat source; if ``(size, mtime_ns)``
         changed since step 1, abort. The 60-second mtime window in
         the candidate filter catches steady-state writers but cannot
         prove a quiet producer won't append during compression; the
         bounded read in step 2 means an append is detected here rather
         than compressed.
      7. ``os.replace`` partial → final, preserve mtime, unlink original.

    Raises ``OSError`` on I/O or codec failure, ``SourceChangedDuringCompression``
    when the source moved under the sweep, and ``RuntimeError`` on any other
    integrity check failure.
    """
    src = Path(src)
    level = codec.check_level(level)
    st_before = src.stat()
    original_size = st_before.st_size
    src_mtime = st_before.st_mtime
    src_fingerprint = (st_before.st_size, st_before.st_mtime_ns)

    dst = compressed_sibling(src, codec)
    tmp = src.with_name(src.name + codec.partial_suffix)
    if tmp.exists():
        tmp.unlink()

    src_hash = hashlib.sha256()

    # Phase 1: compress + hash source + fsync. We open the underlying fd
    # ourselves so ``os.fsync`` has a real fd to call into (the codec
    # writers hide that). The manual chunk loop replaces shutil.copyfileobj
    # so we can hash the source as we read it and stop at the snapshot size.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(tmp), flags, 0o644)
    try:
        try:
            # write-contract: private-staging -- `tmp` is this function's own staging
            # file, published to `dst` by the `os.replace` below only after the bytes
            # are fsynced and verified.
            with os.fdopen(fd, "wb", closefd=False) as raw_fh:
                with (
                    codec.open_write(
                        raw_fh,
                        level=level,
                        source_size=original_size,
                        source_mtime=src_mtime,
                    ) as out_fh,
                    src.open("rb") as src_fh,
                ):
                    remaining = original_size
                    while remaining > 0:
                        chunk = src_fh.read(min(_STREAM_CHUNK, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        src_hash.update(chunk)
                        out_fh.write(chunk)
                    if remaining > 0:
                        raise SourceChangedDuringCompression(
                            f"source shrank during compression for {src}: "
                            f"read {original_size - remaining} of {original_size} bytes"
                        )
                raw_fh.flush()
            os.fsync(fd)
        finally:
            os.close(fd)

        # Phase 2: round-trip integrity check (length + digest).
        verify_hash = hashlib.sha256()
        verified = 0
        with codec.open_read(tmp) as fh:
            while True:
                chunk = fh.read(_STREAM_CHUNK)
                if not chunk:
                    break
                verify_hash.update(chunk)
                verified += len(chunk)
        if verified != original_size:
            raise RuntimeError(
                f"{codec.name} round-trip length mismatch for {src}: "
                f"original={original_size} verified={verified}"
            )
        if verify_hash.digest() != src_hash.digest():
            raise RuntimeError(
                f"{codec.name} round-trip digest mismatch for {src}: "
                f"decoded bytes do not equal source bytes"
            )

        # Phase 3: stability check. If the source changed under us, the
        # round-trip we just verified is against a snapshot that no
        # longer reflects what's on disk; do not delete the original.
        st_after = src.stat()
        if (st_after.st_size, st_after.st_mtime_ns) != src_fingerprint:
            raise SourceChangedDuringCompression(
                f"source changed during compression for {src}: "
                f"started as size={src_fingerprint[0]} mtime_ns={src_fingerprint[1]}, "
                f"now size={st_after.st_size} mtime_ns={st_after.st_mtime_ns}"
            )

        compressed_size = tmp.stat().st_size

        # Phase 4: atomic publish + mtime preservation + delete original.
        os.replace(tmp, dst)
        try:
            os.utime(dst, (st_before.st_atime, src_mtime))
        except OSError:
            # Best-effort: a missing utime won't corrupt anything.
            pass
        if not keep_original:
            src.unlink()
    except BaseException:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
        raise

    return GzResult(
        path=src,
        codec=codec.name,
        original_size=original_size,
        compressed_size=compressed_size,
    )


def gzip_text_path(
    path: Path,
    *,
    include: str = _DEFAULT_INCLUDE,
    min_size: int | None = None,
    codec: Codec = DEFAULT_WRITE_CODEC,
    level: int | None = None,
    keep_original: bool = False,
    exclude_active: bool = True,
    on_compressed: Callable[[GzResult], None] | None = None,
    on_raced: Callable[[Path], None] | None = None,
    on_failed: Callable[[Path, str], None] | None = None,
) -> GzipTextSummary:
    """Compress matching text artifacts under ``path`` without CLI output.

    The CLI and post-run auto-compression use the same byte-verified
    implementation so there is one policy for "reasonable files", and
    one place (``codec``, defaulting to ``DEFAULT_WRITE_CODEC``) that
    decides the encoding. Callback hooks let the CLI preserve per-file
    output without making orchestration code depend on Typer.
    """
    exts = _parse_exts(include)
    if not exts:
        raise ValueError("No extensions to compress; pass --include")
    level = codec.check_level(level)

    files = _walk_files(path)
    thresholds = _build_thresholds(exts, min_size_override=min_size)
    candidates = _classify_candidates(
        files,
        thresholds=thresholds,
        exclude_active=exclude_active,
        active_window_s=_ACTIVE_WINDOW_SECONDS,
    )
    eligible = [c for c in candidates if c.skip_reason is None]
    skipped = [c for c in candidates if c.skip_reason is not None]

    total_orig = 0
    total_out = 0
    failed = 0
    raced = 0
    compressed = 0
    for c in eligible:
        try:
            r = compress_file(c.path, codec=codec, level=level, keep_original=keep_original)
        except SourceChangedDuringCompression:
            raced += 1
            if on_raced is not None:
                on_raced(c.path)
            continue
        except (OSError, RuntimeError) as exc:
            failed += 1
            if on_failed is not None:
                on_failed(c.path, str(exc))
            continue
        compressed += 1
        total_orig += r.original_size
        total_out += r.compressed_size
        if on_compressed is not None:
            on_compressed(r)

    return GzipTextSummary(
        codec=codec.name,
        matched=len(candidates),
        eligible=len(eligible),
        skipped=len(skipped),
        compressed=compressed,
        failed=failed,
        raced=raced,
        original_bytes=total_orig,
        compressed_bytes=total_out,
        saved_bytes=total_orig - total_out,
    )


@app.command("gzip-text")
def gzip_text(
    path: Path = typer.Argument(..., help="File or directory to compress"),
    include: str = typer.Option(
        _DEFAULT_INCLUDE,
        "--include",
        help=(
            "Comma-separated extension list to compress "
            f"(default: '{_DEFAULT_INCLUDE}'). "
            "Example: --include .jsonl,.yaml,.md"
        ),
    ),
    min_size: int | None = typer.Option(
        None,
        "--min-size",
        help=(
            "Override per-extension minimum size (in bytes) for this run. "
            "When omitted, each included extension uses the threshold from "
            "GZIP_DEFAULT_THRESHOLDS (128 KiB for .jsonl/.log, 1 MiB for "
            ".json/.yaml/.yml; 1 MiB fallback for unlisted extensions)."
        ),
    ),
    codec_name: str = typer.Option(
        DEFAULT_WRITE_CODEC.name,
        "--codec",
        help=(
            "Compression codec: "
            + " or ".join(f"'{c.name}' (writes {c.suffix})" for c in CODECS)
            + f" (default: {DEFAULT_WRITE_CODEC.name}). Every codec is always readable."
        ),
    ),
    level: int | None = typer.Option(
        None,
        "--level",
        min=1,
        help=(
            "Compression level; the range and default depend on the codec: "
            + ", ".join(
                f"{c.name} {c.level_range[0]}-{c.level_range[1]} (default {c.default_level})"
                for c in CODECS
            )
        ),
    ),
    keep_original: bool = typer.Option(
        False,
        "--keep-original",
        help="Keep the uncompressed file alongside the compressed one (default: replace)",
    ),
    exclude_active: bool = typer.Option(
        True,
        "--exclude-active/--no-exclude-active",
        help=(
            f"Skip files modified within the last {int(_ACTIVE_WINDOW_SECONDS)}s "
            "to avoid racing live producers (default: on)"
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List candidates without writing anything",
    ),
) -> None:
    """Idempotently compress text-format files.

    Replaces ``foo.jsonl`` with ``foo.jsonl.gz`` (or ``foo.jsonl.zst`` with
    ``--codec zstd``) after verifying the round-trip decodes to the original
    bytes. Default scope is log + structured-document extensions with
    per-extension minimum-size thresholds (see ``GZIP_DEFAULT_THRESHOLDS``).
    Re-runs are a no-op (skips files that already have a compressed sibling).
    """
    out = get_output()
    if not path.exists():
        out.data(f"Path not found: {path}")
        raise typer.Exit(code=1)

    try:
        codec = codec_by_name(codec_name)
        level = codec.check_level(level)
        exts = _parse_exts(include)
    except ValueError as exc:
        out.data(str(exc))
        raise typer.Exit(code=1) from exc
    if not exts:
        out.data("No extensions to compress; pass --include")
        raise typer.Exit(code=1)

    files = _walk_files(path)
    thresholds = _build_thresholds(exts, min_size_override=min_size)
    candidates = _classify_candidates(
        files,
        thresholds=thresholds,
        exclude_active=exclude_active,
        active_window_s=_ACTIVE_WINDOW_SECONDS,
    )
    eligible = [c for c in candidates if c.skip_reason is None]
    skipped = [c for c in candidates if c.skip_reason is not None]

    if not candidates:
        out.data(f"No files matched extensions {sorted(exts)} under {path}")
        return

    if not eligible:
        skip_summary = _summarize_skips(skipped)
        out.data(
            f"No eligible files (extensions={sorted(exts)}, "
            f"min_size={min_size}). Skipped: {skip_summary}"
        )
        return

    if dry_run:
        total_orig = sum(c.size for c in eligible)
        out.data(
            f"Dry-run ({codec.name}): {len(eligible):,} files eligible, "
            f"{fmt_size_human(total_orig)} total"
        )
        est = _estimate_savings_range(eligible, codec=codec, level=level)
        if est is not None:
            low_save, high_save = est
            low_final = total_orig - high_save
            high_final = total_orig - low_save
            out.data(
                f"Estimated -> {fmt_size_human(low_final)} - {fmt_size_human(high_final)} "
                f"compressed (saving {fmt_size_human(low_save)} - {fmt_size_human(high_save)}, "
                f"based on sampled compression ratio)"
            )
        for c in eligible[:50]:
            out.data(f"  would compress ({fmt_size_human(c.size)}): {c.path}")
        if len(eligible) > 50:
            out.data(f"  ... and {len(eligible) - 50:,} more")
        if skipped:
            out.data(f"Skipped: {_summarize_skips(skipped)}")
        return

    failed: list[tuple[Path, str]] = []

    def _on_compressed(r: GzResult) -> None:
        out.data(
            f"  {r.codec}: {r.path}  "
            f"{fmt_size_human(r.original_size)} -> {fmt_size_human(r.compressed_size)} "
            f"({r.reduction_pct:.0f}% saved)"
        )

    def _on_raced(path: Path) -> None:
        out.data(f"  raced (source changed mid-compression): {path}")

    def _on_failed(path: Path, message: str) -> None:
        failed.append((path, message))
        out.data(f"  FAILED: {path}  {message}")

    summary = gzip_text_path(
        path,
        include=include,
        min_size=min_size,
        codec=codec,
        level=level,
        keep_original=keep_original,
        exclude_active=exclude_active,
        on_compressed=_on_compressed,
        on_raced=_on_raced,
        on_failed=_on_failed,
    )

    ratio_pct = (
        (100.0 * summary.compressed_bytes / summary.original_bytes)
        if summary.original_bytes
        else 0.0
    )
    raced_clause = f", {summary.raced:,} raced" if summary.raced else ""
    out.data(
        f"\nDone: {summary.compressed:,} compressed with {summary.codec}, "
        f"{summary.failed:,} failed{raced_clause}, {summary.skipped:,} skipped"
    )
    out.data(
        f"  {fmt_size_human(summary.original_bytes)} -> "
        f"{fmt_size_human(summary.compressed_bytes)}  "
        f"({ratio_pct:.1f}% of original, {fmt_size_human(summary.saved_bytes)} saved)"
    )
    if failed:
        raise typer.Exit(code=1)


def _summarize_skips(skipped: list[GzCandidate]) -> str:
    """Render '12 already-compressed, 3 below-min-size'."""
    if not skipped:
        return "0"
    counts: dict[str, int] = {}
    for c in skipped:
        reason = c.skip_reason or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return ", ".join(f"{n} {r}" for r, n in sorted(counts.items()))


# Per-file sample size for the dry-run ratio estimator. 256 KiB is enough
# to be representative of streamy text formats and small enough that
# sampling a handful of files per extension stays well under a second.
_SAMPLE_BYTES = 256 * 1024
# Files sampled per extension. Files are picked spread across the size
# distribution so a single repetitive outlier (e.g. ``resource-events``)
# doesn't dominate the estimate.
_SAMPLES_PER_EXT = 5


def _sample_compression_ratio(path: Path, *, codec: Codec, level: int) -> float | None:
    """Compress a head-sample of ``path`` at the given level; return ratio.

    Returns ``compressed_size / original_size`` for up to the first
    ``_SAMPLE_BYTES`` of the file. ``None`` on read error or empty file.
    """
    try:
        with path.open("rb") as fh:
            data = fh.read(_SAMPLE_BYTES)
    except OSError:
        return None
    if not data:
        return None
    return len(codec.compress(data, level=level)) / len(data)


def _pick_sample_files(group: list[GzCandidate], n: int) -> list[GzCandidate]:
    """Pick up to *n* files spread evenly across the sorted size distribution."""
    if len(group) <= n:
        return list(group)
    by_size = sorted(group, key=lambda c: c.size)
    step = (len(by_size) - 1) / (n - 1)
    return [by_size[round(i * step)] for i in range(n)]


def _estimate_savings_range(
    eligible: list[GzCandidate], *, codec: Codec, level: int
) -> tuple[int, int] | None:
    """Project a (low, high) savings range across all eligible bytes.

    For each extension, samples up to ``_SAMPLES_PER_EXT`` files spread
    across the size distribution, takes the min and max observed ratios,
    and applies them to the extension's total bytes. Returns ``None``
    when no extension yielded a usable sample.
    """
    by_ext: dict[str, list[GzCandidate]] = {}
    for c in eligible:
        by_ext.setdefault(c.path.suffix.lower(), []).append(c)

    low_savings = 0
    high_savings = 0
    sampled_any = False
    for group in by_ext.values():
        ratios = [
            r
            for c in _pick_sample_files(group, _SAMPLES_PER_EXT)
            if (r := _sample_compression_ratio(c.path, codec=codec, level=level)) is not None
        ]
        if not ratios:
            continue
        sampled_any = True
        ratio_low = min(ratios)
        ratio_high = max(ratios)
        ext_bytes = sum(c.size for c in group)
        # Larger ratio -> bigger compressed file -> smaller savings.
        low_savings += int(ext_bytes * (1 - ratio_high))
        high_savings += int(ext_bytes * (1 - ratio_low))
    if not sampled_any:
        return None
    return low_savings, high_savings
