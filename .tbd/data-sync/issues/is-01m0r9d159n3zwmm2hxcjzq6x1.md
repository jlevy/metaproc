---
type: is
id: is-01m0r9d159n3zwmm2hxcjzq6x1
title: Persist append-only attempt history and replay exact retries
kind: feature
status: closed
priority: 1
version: 16
spec_path: docs/execution-model-design.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r9d1k3vevegz15170dtvcf
  - type: blocks
    target: is-01m0r9d2ckfh04hjx3v2qgafy7
  - type: blocks
    target: is-01m0rady1w8hjdbjz5gvkb5qy8
  - type: blocks
    target: is-01m0ragf2xm87db9bbwhv7ys57
parent_id: is-01m0r93gwcj17mn4dmw1ts7fqa
child_order_hints:
  - is-01m0rax290g9wvpq42d3xmt1yy
  - is-01m0rax25rhvyc6weg0wmpxsm9
  - is-01m0rb5cc2xhbc8mkmfs4vdshj
  - is-01m0rbgdrjj6tcencs8zzekacw
  - is-01m0rbjsad5r3q5zv9pr98d994
  - is-01m0rbzgms1nj43nk8s9w30gt5
  - is-01m0rcmyjhqtfrttsyrkmakd2q
  - is-01m0rd7gxz2jcp1shgy74gjyy8
  - is-01m0rd8k8kvsjqhx5bc6pcmnhp
created_at: 2026-08-23T21:46:05.865Z
updated_at: 2026-08-23T23:56:16.834Z
closed_at: 2026-08-23T23:56:16.834Z
close_reason: "Implemented by Metaproc PR #31 (commit 7563843): append-only typed attempt history, exact retry replay, crash-safe terminal/status projection, task identity validation, execution-seam disposition fixes, fan-out boundary finalization, outputless success, orphan/live-pool reconciliation, and named-worktree portability. Verified by make verify (4,267 passed, 8 skipped) and GitHub Actions lint/distribution/Python 3.12-3.14."
resolution: null
duplicate_of: null
---
Write one typed immutable start fact and one typed terminal fact per actual launch, including generation, fence epoch, disposition, failure class, and timestamps. Retain the legacy attempt.yaml snapshot only for existing readers. Make trace replay consume the facts first and prove the current three-launch retry is represented as three attempts.
