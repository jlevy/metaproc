---
type: is
id: is-01m260z1v7cj4wtcghqr3gvn0k
title: "PR 75 F10: review run-level fail_run policy propagation"
kind: task
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T16:03:43.078Z
updated_at: 2026-09-10T16:03:43.078Z
---
Review decision for existing mp-cl0d: retry.requires_run_abort reports on_invalid: fail_run but execution paths never consume it; a failed item does not implement promised run-wide containment. Require one run-owned abort decision, sibling cancellation/drain, preserved causes and tolerance-policy tests across nested scopes. No duplicated implementation bead; current docs must disclose this limit.
