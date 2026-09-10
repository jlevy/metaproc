---
type: is
id: is-01m26a2gvmth1jjgrkg9fs1vha
title: Expose direct per-agent raw-log drilldown throughout execution
kind: feature
status: open
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m269w80vt7jb8gjanfp3nfj5
child_order_hints:
  - is-01m26abcsm0j8jmm9vrvgkcgta
created_at: 2026-09-10T18:42:53.933Z
updated_at: 2026-09-10T18:54:07.140Z
---
Expose stdout, stderr, and native agent transcript locators for every agent attempt in running, waiting, completed, failed, cancelled, retried, nested, and hydrated/cloud runs. Operator output must identify step/item/attempt and offer exact direct open/tail commands or links to original logs; failure summaries link directly to the relevant evidence. Direct debugging must preserve native payloads and ordering and must not require relying on Metaproc parsing, filtering, trace health, or rollups. Report missing, unavailable, pending-upload, not-downloaded, or not-captured evidence explicitly with retrieval guidance. Keep normal reports bounded and safely redacted without rewriting or discarding original evidence. Update operator manual/generated skill to encourage raw per-agent inspection when diagnosing agent behavior, and test raw-log mapping, retries, relocation, compressed artifacts, and provider-specific availability.

## Notes

Verified current gap: LocalBackend combines stderr into stdout for logged launches, and Pi filter_log/start_log_filter_thread drops native message_update and tool_execution_update records before the only task log is written. Direct file access cannot recover those missing records. Acceptance now requires retaining a raw pre-filter stream, distinguishing combined versus separate captures, source links for every attempt/state, explicit missing/remote/expired evidence, and retention/compaction that does not silently replace the only original transcript. The immediate manual/skill correction is child mp-4wq7; it discloses these current limitations and permits direct read-only debugging without claiming the future capture/UI is delivered.
