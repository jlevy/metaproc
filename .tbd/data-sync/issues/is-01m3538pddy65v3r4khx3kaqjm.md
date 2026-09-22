---
type: is
id: is-01m3538pddy65v3r4khx3kaqjm
title: Resolve the append/replace contract collision on resource-events.jsonl
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:40:23.597Z
updated_at: 2026-09-22T17:40:23.597Z
---
Two writers hold different contracts on `<run>/.logs/resource-events.jsonl`, and one
destroys the other.

- `engine/resource_sampling.py:89` opens it through `ResourceEventLogger`, which is
  `open(path, "a")` holding the handle for the life of the sampler — an **append**
  contract, correctly implemented.
- `engine/resource_rollup.py:146-151` republishes the same path through
  `atomic_output_file` — a **publish-replace** contract, also correctly implemented.

`atomic_output_file` commits with `Path.replace`, which unlinks the old inode. The
sampler keeps its file descriptor on that unlinked inode, so every subsequent
`write()` + `flush()` goes to a file nothing can open. The run silently stops recording
resource samples for the rest of its life.

The rollup does merge `persisted_events` before republishing
(`engine/resource_finalization.py:130-131`), so events written *before* the rename
survive. Only the live sampler's later samples are lost.

`trigger="status"` and `trigger="recovery"` are the paths that can fire mid-run, which
is exactly when a sampler is live.

`filesystem-rules` is the frame: a path has one contract, and routing an append through
replacement weakens it. Two ways out:

1. Have the rollup write its regenerated ledger to a distinct target (e.g.
   `.state/resource-events.rollup.jsonl`) and leave `.logs/resource-events.jsonl`
   append-only. This is the one that makes the contracts true rather than coordinated.
2. Gate the replacing publish on the same terminal condition `finalize_resource_artifacts`
   uses, and document that `trigger="status"` and `trigger="recovery"` must not
   republish while a sampler is live.

Whichever is chosen, the artifact catalog entry for `resource-events.jsonl` should stop
describing one path as both "append-only operational stream" and "atomic rewrite by
rollup".
