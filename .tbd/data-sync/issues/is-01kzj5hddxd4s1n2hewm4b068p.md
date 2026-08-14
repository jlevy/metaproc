---
type: is
id: is-01kzj5hddxd4s1n2hewm4b068p
title: Extract step lifecycle resource events
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-08-09T02:27:26.780Z
updated_at: 2026-08-09T03:05:47.613Z
closed_at: 2026-08-09T03:05:47.613Z
close_reason: "Implemented and verified in combined draft PR #15: code-step CPU/RSS sampling, step lifecycle resource events, and registered ResourcesDocument/0.1 with strict V1/V2 compatibility. Local make verify and the complete GitHub CI matrix pass."
---
Process logs contain step_start, step_complete, and step_fail records with elapsed_s, but the resource-event union and extractor only represent item lifecycle events. Add typed step lifecycle events, extract them, and prove code-step wall time reaches the owning step rollup.

## Notes

Implemented StepStartEvent, StepCompleteEvent, and StepFailEvent in the resource union; process-log extraction maps elapsed_s/error to the owning hierarchy node. Process lifecycle hierarchy deliberately omits the shared process-events file_path so multiple steps cannot collapse onto one file node. Focused and full verification are green.
