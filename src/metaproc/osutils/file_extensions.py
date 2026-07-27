"""Centralized file-extension settings for the browser + gzip-text.

Four independent sets, each scoped to one decision:

* :data:`BROWSER_TEXT_EXTS` — extensions the browser opens as text in
  the detail pane *regardless of file size*. Extensions outside this
  set still render as text when the file is small (under the inline
  fallback cap), but above that threshold they fall through to binary.
  Includes web formats, structured data, tabular, config, source
  code, and metaproc-specific suffixes.

* :data:`BROWSER_IMAGE_EXTS` — extensions the browser renders as
  images via ``<img>``.

* :data:`BROWSER_TRACKABLE_EXTS` — extensions the activity tracker
  watches for live writes. Kept tight because every entry costs one
  ``stat()`` per poll on the candidate-discovery walk; only formats
  metaproc actually appends to at runtime belong here. Notably
  excludes ``.gz`` (write-once-sealed) and source code (not produced
  at runtime).

* :data:`GZIP_DEFAULT_INCLUDE` — extensions ``metaproc gzip-text``
  compresses when no ``--include`` flag is passed. Deliberately a
  *strict subset* of the browser-readable formats: gzipping any
  browser-text extension would round-trip cleanly through the
  ArtifactPath layer (the browser handles any ``*.gz`` suffix
  transparently), but markdown / plain text / tabular files are
  commonly opened in editors or grepped from the shell where the
  ``.gz`` adds friction.

Extensions beyond ``GZIP_DEFAULT_INCLUDE`` remain fully browsable
when manually gzipped — the browser's ``ArtifactPath`` layer routes
any ``*.gz`` suffix through ``open_text`` / passthrough, so
``foo.html.gz`` opens as HTML, ``foo.xml.gz`` as XML, etc. The
default just narrows what the sweep tool touches unprompted.
"""

from __future__ import annotations

# Extensions the browser opens as text in the detail pane regardless
# of size. Outside this set, files still render as text when smaller
# than the inline fallback cap (see ``proc_browser._INLINE_TEXT_FALLBACK_BYTES``);
# above that threshold they fall through to binary.
BROWSER_TEXT_EXTS: frozenset[str] = frozenset(
    {
        # Documents
        ".md",
        ".txt",
        ".rst",
        ".markdown",
        # Web / markup
        ".html",
        ".htm",
        ".xml",
        # Structured data
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        # Tabular
        ".csv",
        ".tsv",
        # Config
        ".cfg",
        ".ini",
        ".env",
        # Source code
        ".py",
        ".sh",
        ".bash",
        ".zsh",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".css",
        # Database
        ".sql",
        # Logs (separate from .jsonl which gets structured rendering)
        ".log",
        # Metaproc-specific
        ".pid",
    }
)

# Extensions the browser renders as images via ``<img>``.
BROWSER_IMAGE_EXTS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".ico",
        ".avif",
        ".apng",
    }
)

# Extensions the activity tracker watches for live writes. Keep tight:
# every entry costs a stat() per poll on the candidate discovery walk,
# and only formats metaproc actually appends to belong here. ``.gz`` is
# deliberately excluded (write-once-sealed by gzip-text construction).
BROWSER_TRACKABLE_EXTS: frozenset[str] = frozenset(
    {
        ".jsonl",
        ".yaml",
        ".yml",
        ".pid",
        ".json",
        ".md",
        ".csv",
    }
)

# Per-extension minimum-size thresholds for ``metaproc gzip-text`` defaults.
# Keys are the extensions the sweep considers; values are minimum file size
# in bytes. Tuned per artifact shape:
#
# * Log-shaped formats (``.jsonl``, ``.log``) get the lower 128 KiB floor.
#   They appear in high volume (every run produces hundreds), get viewed
#   through tools rather than rendered diffs, and the gitignored long
#   tail under ``**/.state/`` + ``**/.logs/`` is where the real disk win
#   lives — that subset isn't in git anyway, so diff readability never
#   enters the trade.
# * Structured-document formats (``.json``, ``.yaml``, ``.yml``) get the
#   higher 1 MiB floor. Most tracked instances of these are small
#   (precedent files, postprocess reports, fixtures, configs), and small
#   tracked artifacts get reviewed/grepped/PR-diffed constantly — losing
#   GitHub's rendered view + git pack delta compression for ~50 KB of
#   on-disk savings is the wrong trade. The 1 MiB floor leaves room for
#   the genuine outlier (a multi-MB cohort report) without touching the
#   thousands of small structured artifacts.
#
# Sentinels:
#
# * ``0``  — always gzip (no minimum). Useful for any-size-is-worth-it
#   policies, e.g. an operator-only ``.dump.tmp`` extension.
# * ``-1`` — never gzip. Explicit denylist: keeps the extension in this
#   dict for documentation but refuses to compress it even when an
#   operator passes ``--include`` for it. Intended for known-hot-edit
#   extensions that should always stay raw.
#
# Operators with one-off needs override per-call via ``--min-size`` (a
# global floor for the run) and ``--include`` (extension subset).
GZIP_DEFAULT_THRESHOLDS: dict[str, int] = {
    ".jsonl": 128 * 1024,  # 128 KiB
    ".log": 128 * 1024,  # 128 KiB
    ".json": 1024 * 1024,  # 1 MiB
    ".yaml": 1024 * 1024,  # 1 MiB
    ".yml": 1024 * 1024,  # 1 MiB
}

NEVER_GZIP: int = -1
"""Sentinel for ``GZIP_DEFAULT_THRESHOLDS`` values: refuse to gzip even when explicitly requested."""


def gzip_default_extensions() -> tuple[str, ...]:
    """Extensions enabled by the gzip-text default sweep (excludes ``-1`` denylist entries)."""
    return tuple(ext for ext, t in GZIP_DEFAULT_THRESHOLDS.items() if t != NEVER_GZIP)


def gzip_default_include_csv() -> str:
    """Comma-separated form for the typer ``--include`` default."""
    return ",".join(gzip_default_extensions())


def gzip_threshold_for(ext: str, *, fallback: int) -> int:
    """Minimum byte size for ``ext``; ``fallback`` when not in the defaults table."""
    return GZIP_DEFAULT_THRESHOLDS.get(ext.lower(), fallback)
