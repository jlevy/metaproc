---
type: is
id: is-01m0tqcwfnjqp09gdq29z7pq6x
title: Replace cloud topology flags with explicit orchestrator and worker placement
kind: feature
status: open
priority: 1
version: 3
labels:
  - cloud
  - architecture
dependencies: []
created_at: 2026-08-24T20:29:10.005Z
updated_at: 2026-08-24T22:12:03.388Z
---
Make run-process the application-level orchestration API and keep gcp run as a lower-level one-task primitive. Replace the public --backend gcp-worker plus --cloud combination atomically with --orchestrator and --worker, resolved into an immutable provider-neutral topology containing orchestrator placement, one run-wide worker placement/resource profile, and a compatible state transport. Keep LaunchBackend internal. Initially all workers share one placement/profile; allow later per-step overrides without changing the process engine or CLI axes. Reject split-locus placement until a real bidirectional transport exists, and add no historical runtime compatibility layer.

## Notes

PR 38 commit 060e5be documents the current run-process/gcp-run boundary, target --orchestrator/--worker vocabulary, one homogeneous run-wide worker placement initially, immutable topology/provider boundary, and fail-closed state-transport requirement. It now distinguishes SSH host control from pipeline state and links the maintained open-source MetaBrowser SSH utilities and remote command for future reuse or extraction. Documentation and CLI help are complete; runtime flag migration, topology type, transport resolver, and acceptance smokes remain open. Exact-head CI run 32782930579 passed all five jobs.
