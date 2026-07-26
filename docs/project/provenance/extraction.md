# Extraction Provenance

Date: 2026-07-26

## Source Baseline

Metaproc was extracted from a sealed monorepo tree without importing its Git history.
The source ledger was generated from Git objects before any standalone rewrite.

| Field | Value |
| --- | --- |
| Source commit | `a0701d44a5d7dd260ddf2741f2a032a55fd25c1c` |
| Source tree | `b67be525f6b019e98a63184c1954d7da3db7d942` |
| Classified source paths | 589 |
| Byte-exact copy paths | 291 |
| Public rewrites | 127 |
| Synthetic replacements | 158 |
| Consumer moves | 2 |
| Deferred public items | 2 |
| Private-history exclusions | 9 |

Every byte-exact destination was copied as a complete file and checked with
`git hash-object` against the source ledger before standalone edits.
The checkpoint had 291 matches, no missing destinations, and no mismatches.

An ignored-path audit after the initial root commit found that the exact
`tests/fixtures/claude_api_signals/oauth_refresh_400.log` blob remained in the worktree
but had been omitted from the commit by the scaffold’s generic `*.log` rule.
Its source and destination Git blob hashes were both
`f189a0a2fc1cdc4b8d93b4cb64a9428865877e46`. The fixture was force-added, the ignore rule
was narrowed, and all other classified source paths were checked against the standalone
ignore rules; no other source file was omitted.

## Intentional Post-Copy Changes

Files classified for rewriting or synthetic replacement were changed only after the
complete source blob had been copied and verified.
Three initially exact infrastructure files then required standalone-specific changes:

- `.gitignore` was replaced by the rendered repository template and extended for the
  locked JavaScript toolchain.
- `devtools/public_hygiene.py` gained an exact allowlist entry for the maintainer email
  published in package metadata; its negative email tests remain in force.
- `src/metaproc/plugins/registry.py` dropped the obsolete `Contract.owner` argument so
  the extracted registry matches the public `softschema` 0.1.4 contract API.

No framework implementation file was reconstructed from memory.
Each intentional change started from the verified complete source blob.

## Repository Scaffolding

The standalone scaffold was rendered from
[`jlevy/simple-modern-uv`](https://github.com/jlevy/simple-modern-uv) v0.4.0 at commit
`69266fba53677b1a904f3afd568ccfc1ab735e21`. The rendered AGPL license, Copier answers,
workflows, and developer files were copied as complete files before project-specific
adaptation.

Repository structure and release hardening were compared against
[`jlevy/metabrowser`](https://github.com/jlevy/metabrowser) at commit `f5d8cc4`.
Metaproc-specific metadata, package contents, optional cloud dependencies, CLI smoke
tests, and browser-plugin boundaries remain independently defined here.

## History Boundary

This repository starts with a clean public history.
No source commits, tags, refs, reflogs, issue-tracker objects, credentials, run
artifacts, or private fixtures were grafted into it.
The public-hygiene gate scans repository files, built archives, and all reachable Git
metadata before release.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
