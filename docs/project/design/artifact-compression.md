---
title: Artifact Compression
description: One codec model for gzip and Zstandard, two sizes that no caller picks by codec, logical-stream identity across encodings, and what gates the switch to zstd as the write default.
date: 2026-10-20
status: Design record; implemented
---
# Design: Artifact Compression

**Date:** 2026-10-20

**Status:** Design record.
Implemented in `metaproc.io.compression`, `metaproc.io.artifact_io`, the `metaproc tail`
reader, and `metaproc gzip-text`.

## Overview

Metaproc stores large completed artifacts compressed.
Until this record, the only codec was gzip, and the knowledge of what that meant was
spread across callsites: `.gz` suffix tests in a dozen modules, a reader that chose
between two sizes by asking whether the file was gzipped, and `except OSError` blocks
that did not catch the exceptions gzip actually raises.
Adding a second codec by the same method would have doubled every one of those sites.

The design here answers one question per concern, each in one place:

- **Which codecs exist**, and everything specific to one of them: a closed registry of
  two, gzip and Zstandard, in `metaproc.io.compression`.
- **What a size means**, independent of codec: the stored size and the decoded size, on
  `ArtifactPath`, with the uncompressed size treated as metadata the writer records
  rather than a property derived from the compressed bytes.
- **What a logical stream is** when it may be stored under three names, and which file
  holds it: `metaproc.io.artifact_io`.
- **What a reader does** when the stored encoding changes under it: the `metaproc tail`
  reader, which no longer measures a compressed stream at all.
- **What Metaproc writes**, and what gates changing that: one assignment,
  `DEFAULT_WRITE_CODEC`.

The review comment that started this asked for the sizes to be encoded separately from
the codecs, for zstd to work seamlessly beside gzip, and for zstd to become the default
in the future. Each is addressed below; the third is deliberately not done yet, and the
section on write policy says what has to happen first.

## The Codec Model

`metaproc.io.compression` defines `Codec`, an abstract class with one instance per
supported codec, and the registry that holds them:

| Fact | gzip | zstd |
| --- | --- | --- |
| `name` (also the `compression` tag in API envelopes) | `gzip` | `zstd` |
| `suffix` | `.gz` | `.zst` |
| `partial_suffix` (a file being written; never an artifact) | `.gz.partial` | `.zst.partial` |
| `content_encoding` (HTTP passthrough) | `gzip` | `zstd` |
| `default_level`, `level_range` | 6, 1–9 | 3, 1–22 |
| `decode_errors` translated by the reader | `BadGzipFile`, `EOFError`, `zlib.error` | `ZstdError`, `EOFError` |
| backend | `gzip` | `compression.zstd` (standard library, Python 3.14) |

Each codec owns its reader (`open_read`), its writer (`open_write`), a one-shot
`compress` for estimates, and `recorded_size`, the decoded size its container stores.
`CODECS` lists them in tie-break order; `DEFAULT_WRITE_CODEC` names the one Metaproc’s
own writers use; `codec_for_path` is the only suffix test in the codebase.
`ArtifactPath.codec` consults it once, and every other property and method hangs off
that.

There is no plug-in mechanism.
The set is closed on purpose: a reader that meets a suffix it does not know treats the
file as plain, which is the right behavior for `.partial` and for anything else, and a
writer that is offered a codec name outside the set is refused with the valid choices.

Zstandard comes from the standard library.
That is why the package’s Python floor is 3.14: `compression.zstd` exists from that
release, and the alternative — an optional third-party backend behind an import guard —
would have shipped a zstd that works only on the interpreters where the standard library
already has it, with more code and a worse story.
Raising the floor was the smaller change.

## Size Semantics

Two sizes exist, and neither depends on the codec:

- **`disk_size`**: the bytes the file occupies as stored.
  What `du` counts, what a passthrough response serves, what a disk-reclaim report adds
  up.
- **`logical_size`**: the bytes of the decoded stream, what `open_binary()` yields end
  to end. What a size threshold tests, what a cache key records, what an operator reads
  as “how big is this transcript”.

For a plain file they are equal.
For a compressed one, `logical_size` is read from the container when the container
records it, and counted by decoding when it does not.
No caller picks between the two by asking which codec is in use; the codec is how
`logical_size` is obtained, not what it means.

The decisive fact behind this split: **the uncompressed size is recorded metadata, not a
property derived from the compressed bytes.** A Zstandard frame header carries a
content-size field that is optional; a streaming writer omits it unless the size is
pledged up front, and `compression.zstd.ZstdFile` does not pledge it.
A gzip trailer always carries ISIZE, but ISIZE is the size of the final member modulo
2³², so a concatenated multi-member file reports the last member alone.
Neither container gives a sound exact length for a file it did not itself write
completely, in one member or frame, with the size known.

So the contract is stated at the codec, as `Codec.recorded_size(path) -> int | None`:

- **Exact for artifacts Metaproc writes.** `compress_file` stats the source first and
  passes `source_size` to `open_write`; the zstd writer pledges it with
  `ZstdCompressor.set_pledged_input_size`, so the frame header carries it, and gzip
  writes ISIZE as always.
  One member or frame per file.
- **The container’s figure for a foreign artifact**, documented per codec: gzip’s
  last-member ISIZE, zstd’s first-frame content size.
- **`None` when nothing is recorded**, which a `.zst` from the `zstd` CLI or from a
  streaming writer legitimately is.
  `ArtifactPath.logical_size` then decodes and counts rather than guessing, at O(n)
  cost, so the property is always a true length.
- **Never a bound to compare an offset against.** A reader tracking its position in a
  decoded stream must not use this figure to decide whether more remains; a file holding
  several members or frames is complete at a size the figure does not describe.
  The reader design below is built so that the comparison cannot be expressed.

## One Error Type

Backend failures are not `OSError`s. A truncated gzip or zstd stream raises `EOFError`;
corrupt deflate data raises `zlib.error`; a corrupt zstd frame raises `ZstdError`. Only
`BadGzipFile` subclasses `OSError`. Before this record, every reader that wrote
`except OSError` around a compressed read had a hole, and `metaproc tail` could be
crashed by a `.gz` damaged on disk or still being copied into a watched directory.
With a second codec the hole would have needed
`except (OSError, EOFError, zlib.error, ZstdError)` at every site, which is the
per-codec knowledge the abstraction exists to keep out of callers.

The codec layer owns it.
`Codec.open_read` wraps the backend handle in a stream that translates every exception
in that codec’s `decode_errors` into one `ArtifactDecodeError`, and
`ArtifactDecodeError` subclasses `OSError`. The choice of an `OSError` subclass over a
new base type was deliberate: every existing `except OSError` around an artifact read
became correct without an edit, an artifact that cannot be decoded is treated exactly
like one that cannot be opened, and a caller that wants to tell the two apart can still
name the subclass. The size probe follows the same rule; a `.zst` whose header is not a
frame raises `ArtifactDecodeError` rather than `ZstdError`. On the write side a pledge
the writer would exceed raises `OSError` too.

## Logical-Stream Identity

A logical artifact such as `attempt.jsonl` may exist on disk as `attempt.jsonl`,
`attempt.jsonl.gz`, or `attempt.jsonl.zst`, and transiently as
`attempt.jsonl.gz.partial` while a writer stages it.
`logical_path` strips one codec suffix and nothing else, so a `.partial` is not an
artifact and is never matched by the discovery globs.

Preference when more than one encoding exists:

1. The plain file. It may still be growing; a compressed file is sealed, and the
   compaction sequence publishes the compressed file before unlinking the plain one, so
   the plain file is the newer of the two while both exist.
2. Compressed encodings in `CODECS` order, gzip before zstd.
   This is a tie-break, not a policy: Metaproc never writes a second encoding of a
   stream that already has one, so two compressed siblings arise only by hand.

`resolve_existing_artifact`, `artifact_exists`, and `iter_artifact_paths` apply that
order; `iter_artifact_paths` globs the logical pattern plus each codec suffix and keeps
the first file per logical path.

A reader that follows a stream across an encoding change is the `metaproc tail` reader,
`LogFile`. Its contract:

- Identity comes from `fstat` on the handle the bytes are read through, never from a
  separate `stat` of the path, so a replacement between two polls is never read at the
  previous stream’s offset.
- A **plain stream** grows in place.
  It is read from the retained offset; a size below the offset means a rewrite, and the
  reader discards every derived field (`_reset_read_state`) before replaying from the
  start.
- A **compressed stream** is immutable once published.
  Its identity is the inode plus size and mtime; it is read in full the first time that
  identity is seen, marked sealed, and never measured again.
  Any change to inode, size, or mtime is a replacement: full reset, replay, one warning.
- A stream that cannot be opened or decoded yields nothing this poll and is retried on
  the next; a decode failure warns once per identity, so a `.gz` still being copied in
  produces one line, not one per second.

Replayed events may repeat earlier display output.
Metadata is replaced, not summed, which is why the reset is total.

## Write Policy

`metaproc gzip-text` and the post-step log pass share one writer, `compress_file`, which
takes a codec and defaults to `DEFAULT_WRITE_CODEC`. The CLI exposes it as
`--codec gzip|zstd`; the command keeps its name because the name is a public flag
surface. The writer reads exactly the source’s snapshot size, so a producer that appends
mid-sweep is detected by the stability check as a race rather than compressed, and
verifies the round trip by digest and length before publishing.

**Today’s default is gzip.** Zstandard is the intended successor: on a repetitive JSONL
sample, zstd at its default level compressed about 2.2× smaller than gzip at its default
(307× against 138×), and it decodes faster.
That figure is from a synthetic, highly repetitive sample; real transcripts compress
less dramatically, and the ratio between the two codecs will be smaller.

The switch is the one assignment `DEFAULT_WRITE_CODEC = ZSTD`. Reading needs no change,
because every registered codec is always readable.
What gates it:

1. **Metabrowser reads `.gz` and `.zlib`, not `.zst`.** The browser serves files through
   its own `ArtifactPath`, and the Metaproc plugin’s chart handler uses that class, so a
   `.zst` transcript today renders as an opaque binary and gets no charts.
   Metabrowser must learn the codec first, and that release must be adopted here.
2. **Downstream readers.** Any consumer package that reads run directories with its own
   file code needs the same `.zst` awareness, or needs to move to `metaproc.io`.
3. **Operator habit.** The operator reference shows `gzip -cd`; it now shows `zstd -cd`
   beside it, but a default change is a release-note item.

The per-extension size floors in `metaproc.osutils.file_extensions` decide whether a
file is worth compressing at all and are the same for every codec.
A 128 KiB `.jsonl` floor is about keeping small tracked artifacts raw for diff and
search, not about any codec’s overhead.

## Compatibility

- Existing `.gz` artifacts stay readable indefinitely; nothing re-encodes them.
- A run directory holding `.gz` and `.zst` artifacts side by side is ordinary, not an
  error state. Every reader resolves each logical stream independently.
- The sweep never touches a compressed file and skips a plain file that already has a
  compressed sibling in any codec, so one logical stream is stored in one encoding.
- `ArtifactPath.is_gzip` is gone; `is_compressed`, `codec`, and `compression` replace
  it, and nothing in the tree branches on a codec.
  `metaproc.io.gz_io` is `metaproc.io.artifact_io`; the curated `metaproc.io` surface
  gains `ArtifactDecodeError` and `artifact_sidecar_path` and loses nothing.
- `commands.gzip_text.gzip_file` is `compress_file(src, *, codec=...)`;
  `GzipTextSummary.gzipped` is `compressed`, and both result types carry `codec`.
- The Python floor is 3.14. An environment on 3.12 or 3.13 cannot install this version,
  and the cloud image’s `/opt/venv` must be on 3.14 before a wheel built from it is
  shipped there.

## References

- `src/metaproc/io/compression.py`
- `src/metaproc/io/artifact_io.py`
- `src/metaproc/logutil/parsing.py` (`LogFile`)
- `src/metaproc/commands/gzip_text.py`
- [arch-file-io-utilities.md](../../../src/metaproc/docs/arch-file-io-utilities.md)
- [RFC 8878, Zstandard](https://www.rfc-editor.org/rfc/rfc8878)
- [RFC 1952, gzip](https://www.rfc-editor.org/rfc/rfc1952)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
