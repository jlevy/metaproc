---
type: is
id: is-01m5x6qsqqtjxt5m417w8z7y7c
title: "PR 117 Q1-Q7: signal-exit registration, outcome snapshot and live ledger"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
created_at: 2026-10-26T22:54:14.263Z
updated_at: 2026-10-26T23:42:13.820Z
closed_at: 2026-10-26T23:42:13.820Z
close_reason: "Fixed on PR #117 at 90e0bb6. Q1: the signal-exit work is registered before the hard-stop handlers are installed, with cleanup if installation fails; boundary-signal regression fails on the previous head with no summary at all. Q2: terminal_error_type carried forward from the recorded outcome. Q4: the body publishes one frozen outcome-and-error snapshot at each resolution point. Q5/Q6: owner-aware lock plus an in-process lease handoff for signal-exit publication. Q7: write_events=False on the signal path, with an inode regression. Q3 answered on its thread: the mark must stay lock-free, and the durable record plus the under-lock comparison is what makes it safe. Every thread replied to; six of seven resolved, Q3 left open for the owner's judgement on that design decision."
resolution: null
duplicate_of: null
---
Qodo review on PR #117, 2026-10-26.

Q1 (High): run_process_command installed the hard-stop handlers before registering the signal-exit work, so a signal in that window found an empty registry and wrote nothing. Registration now precedes installation, with cleanup if installation fails.
Q2: status recovery lost the terminal_error_type of the launch it rebuilt; it now carries it forward from the recorded outcome.
Q4: the body's outcome and its error are published as one frozen snapshot at each resolution point instead of two stores in the terminal finally.
Q5/Q6: the finalization lock is owner-aware and signal-exit work inherits a lock its own process holds (shared with the A1-A4 work).
Q7: signal-exit finish passes write_events=False, so it no longer replaces .logs/resource-events.jsonl while the psutil samplers append to it.
Q3: answered on the PR thread — the mark stays lock-free by design and the causal record plus the under-lock comparison make it safe.

Regressions in tests/test_signal_exit_resource_summary.py and tests/test_finalization_outcome.py.
