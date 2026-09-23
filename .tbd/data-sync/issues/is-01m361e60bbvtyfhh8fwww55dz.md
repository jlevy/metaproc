---
type: is
id: is-01m361e60bbvtyfhh8fwww55dz
title: "PR #96 review design: name the response to a changed input instead of a boolean"
kind: task
status: closed
priority: 1
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m361e3we0dyegxyvvw63a9vk
hold: null
hold_until: null
created_at: 2026-09-23T02:27:40.682Z
updated_at: 2026-09-23T03:00:45.139Z
started_at: 2026-09-23T02:29:18.939Z
closed_at: 2026-09-23T03:00:45.138Z
close_reason: "Adopted in 72a83bd: the authored field is on_change: record | new_run (record the default, byte-for-byte today's behavior; new_run implemented as per-scope bindings). rerun is not shipped and is designed in src/metaproc/docs/execution-model-design.md, 'Inputs: Identity, Reuse, and Evidence'; follow-ups mp-p3hk (rerun), mp-c1aa (per-attempt launch context and effective bindings), mp-slg2 (launch-time pre-check of scalar composite child bindings)."
resolution: null
duplicate_of: null
---
The review's design assessment: the missing distinction is between work identity, reuse validity and execution evidence; important inputs must control reuse without becoming immutable for the life of a run. Recommended authored contract: an on_change enum on a process input, record (default: record the new value and continue), rerun (continue and invalidate affected consumers), new_run (require a different logical run). A boolean identity flag selects behavior, so the field should name the behavior. Do not ship a rerun value until it changes reuse behavior. Decide the contract for this PR, write the reasoning into the PR reply and the execution-model design doc, and keep the behavior of every spec that declares nothing.
