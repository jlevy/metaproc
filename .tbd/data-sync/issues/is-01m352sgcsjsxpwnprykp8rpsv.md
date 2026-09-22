---
type: is
id: is-01m352sgcsjsxpwnprykp8rpsv
title: "[epic] Atomic file publication and filesystem-rules compliance"
kind: epic
status: open
priority: 1
version: 21
labels: []
dependencies: []
child_order_hints:
  - is-01m352yg8gvb8xd6ekwvmmqzm7
  - is-01m352ygpj0tbh6tr4fdp6ebyt
  - is-01m3538nn6crc2m9jbaf26yjae
  - is-01m3538p1ap1awtpng65kzznea
  - is-01m3538pddy65v3r4khx3kaqjm
  - is-01m3538psm11yx7j3erwgac5q4
  - is-01m3539g0x1gjf0z2mkvbpxe3q
  - is-01m3539gcv2v238gjdrw3v4ee7
  - is-01m353aeczswk6fxynyghjgt8v
  - is-01m353aerqjn884pe6nbfgk27h
  - is-01m353af4mtmfm1rp5csvvzdgt
  - is-01m353be1e1hxdc6k5g7r51z08
  - is-01m353bed9hepjt1n3wfd0a6w1
  - is-01m353bev396et47d2gp6mep15
  - is-01m353bf8bkxvpc05ahmtc8888
  - is-01m353cc0dcqwnvk67wx16m39h
  - is-01m353cccy7ppxy82ewphvvqg6
  - is-01m353dbbtcnx5ft2achfvzy2s
  - is-01m353dbr89c9fm5bcfc1a5r66
created_at: 2026-09-22T17:32:05.913Z
updated_at: 2026-09-22T18:03:27.189Z
---
Metaproc writes run state, reports, manifests, leases, and artifacts to disk from many
modules. Most of that already goes through Strif's `atomic_output_file`, but the rule was
advisory: nothing prevented a new code path from calling `Path.write_text` on a published
path, and nothing named the contract a given write is supposed to honor. Recent work in
`commands/run_process.py` added more of the `mkdir(parents=True, exist_ok=True)` plus
`with atomic_output_file(...) as tmp: tmp.write_text(...)` shape, which is atomic but
restates the parent-directory step Strif already offers.

`tbd guidelines filesystem-rules` and `python-modern-guidelines` set the standard this
epic applies:

- Every write has exactly one contract, and the code should say which:
  publish-replace, publish-create-only, append, live stream, or private staging.
- Contracts 1 and 2 require atomic publication: stage a temp file in the destination
  directory, then commit it in one step.
- Routing an append or a live stream through replacement *weakens* it, so those stay as
  they are and are documented as deliberate.
- The rule has to be executable at the boundary where it applies, not advisory.

Scope of this epic:

1. Audit every file-mutation site in `src/metaproc/` and classify it by contract.
2. Fix every write that needs atomic publication and does not have it.
3. Collapse the mkdir-then-stage boilerplate onto Strif's `make_parents=True`.
4. Record the deliberate exceptions (append logs, live streams, private staging) so the
   exception list is finite, named, and reviewable rather than implicit.
5. Add a project check that keeps the rule enforceable, following the
   `devtools/check_plc0415_justifications.py` precedent, and wire it into `make lint`.
6. Fix the adjacent filesystem-rules shortcomings the audit turns up: nondeterministic
   traversal order where order is observable, swallowed traversal errors, unverified
   recursive deletes, implicit symlink following, cross-device renames, and success
   reported after partial failure.

Done when: the audit is complete, every violation is fixed or tracked with a named
reason, the exception list is written down, and the check fails on a new violation.

## Notes

## Landed

Six children are closed. What shipped:

- **mp-9ii5** — `devtools/check_atomic_writes.py`, wired into `devtools/lint.py`, so
  `make lint-check` and CI enforce it with no workflow change. The package now has
  **zero** unnamed truncating writes: every one either publishes atomically or carries
  a `# write-contract:` marker with a reason.
- **mp-op97** — every violation fixed. Nine credential sites moved off write-then-chmod
  onto `metaproc.io.write_secret_text`, which creates the staged file at `0o600` before
  the first byte lands. `~/.pi/agent/models.json` had no `chmod` at all.
- **mp-buz2** — `metaproc.io` gained a named helper per contract, and roughly
  twenty-five mkdir-then-stage sites collapsed onto `atomic_write_text(..., make_parents=True)`.
- **mp-9lm5** — `PLW1514` enabled through `explicit-preview-rules`, 120 sites fixed.
- **mp-bx26** — the two atomicity tests that could not fail replaced with failure
  injection.
- **mp-fk8o** — nine traversals sorted where the order was observable.

## Worth knowing for the remaining children

The atomicity test in mp-bx26 taught something that applies to the rest of this epic.
The first version injected `data[: len(data) // 2]`, and it **passed against a
deliberately non-atomic writer**. The rebuild appends a second item's events, so the
first half of the new file is byte-identical to the old one: a truncated destination
looked exactly unchanged. The fix was to inject a sentinel rather than a prefix.

Any test written for the remaining beads should be mutation-checked the same way —
break the implementation on purpose and confirm the test fails. Several of the open
children (mp-xhr6, mp-f588, mp-32zk) are concurrency bugs where a test that cannot
fail is the likeliest outcome.

## What is left

Thirteen children, none of them mechanical. Each changes behaviour and wants an
owner's decision rather than a sweep:

- Two reclaim races that break mutual exclusion (mp-xhr6, mp-f588).
- One path with two contracts on it, where the atomic writer unlinks the inode a live
  appender holds open (mp-32zk).
- Three places that report success over a failure: a deleted post-mortem (mp-r16j), a
  write-boundary check that passes when it could not run (mp-l96t), and batch commands
  exiting zero after partial failure (mp-2rmg).
- Log compaction against a live log (mp-t7c2) and the gzip staging collision (mp-rzoe).
- The rest are smaller: mp-uy5y, mp-0hkt, mp-n0fg, mp-k29g, mp-a3mr.
