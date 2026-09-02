---
type: is
id: is-01m1dx1c2x091evxbembw1dnxn
title: Build standalone process-safety runtime; defer pool extraction
kind: feature
status: in_progress
priority: 1
version: 12
spec_path: docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md
labels: []
dependencies: []
created_at: 2026-09-01T07:13:18.428Z
updated_at: 2026-09-02T02:49:03.225Z
---

## Notes

Plan published in https://github.com/jlevy/metaproc/pull/62. Current recommendation: one cross-platform repository for a shared process-safety core with two first-class, non-substitutable contracts. `SafeProcess` owns a launch and establishes admission, isolated process-group identity, containment, and cleanup before target execution. `ProcessMonitor` accepts a PID/create-time-fenced `ProcessTarget` and returns a `MonitoredProcess` handle for an existing tree. Monitoring remains brokerless-capable and observes and journals by default; signalling requires explicit authority and descendant revalidation. The name is intentional: this mode does not imply parentage, ptrace, ownership, exit prevention, or another operating-system attachment. Both modes share host telemetry, pressure classification, policy, journals, replay, and platform backends. Thin `run`, `watch`, `status`, and `replay` CLIs are adapters over the Python library.

The first package deliberately has no `SafeRunPool`, submission queue, pool-result types, or `pool` CLI. Metaproc retains its existing RunPool as the only queue and adaptive-capacity controller and integrates the package through `SafeProcess`. Pool extraction is deferred to optional Phase 4 after the package and retained-RunPool seam have representative operating and maintenance evidence. A later spike must exercise the seven extraction gates before any public pool API or compatibility facade is published; no-go remains an acceptable permanent result.

Procguard v1.5.1 was checked out under ignored `attic/` and reviewed statically. It is a useful macOS owned-process and failure-corpus reference, not a dependency candidate: its ordinary `posix_spawn`/process-group path, kqueue loop, clock split, result schema, and race tests inform the plan; its root-PID-only accounting, fork-on-resource-limit path, global signal singleton, incomplete error cleanup, macOS-only backend, and orchestration feature growth become explicit constraints and tests.

Research update at 18f3f7d: audited the refreshed downstream process-memory records section by section and made three focused, project-neutral Metaproc documents authoritative. The cross-client record preserves the matched Gemini, Claude, Codex, and Pi profiles, profile identity, production curve, invalid historical sampler caveat, and open benchmark matrix. The Gemini record preserves the 0.40.1 and 0.55.1 causal controls, 0.58.0 source path, durable session-store semantics, growth mechanism, heap-cap failure, and fresh-versus-resumed state boundary. The host-control record preserves macOS and Linux budget and attribution semantics, guard incidents, admission, pacing, containment, and failure reconstruction. The active plan permits the low Gemini profile only after mitigation is verified, keeps the high-spike fallback otherwise, and rejects V8 heap caps as admission control. Consumer-specific identifiers, paths, deployment tasks, and raw artifacts remain with their owner.

Naming and first-release scope finalized at 1cce041. Local `make verify` and the pre-push gate passed with 4,580 tests and 8 skips; lint, links, type checks, browser checks, audits, distribution inspection, and installed-wheel smoke passed. All five jobs in https://github.com/jlevy/metaproc/actions/runs/33574595299 passed on Python 3.12 through 3.14. Keep open for phased implementation.
