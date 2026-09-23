---
type: is
id: is-01m352ygpj0tbh6tr4fdp6ebyt
title: Replace the atomicity tests that cannot fail with failure injection
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:34:50.065Z
updated_at: 2026-09-22T18:03:00.855Z
closed_at: 2026-09-22T18:03:00.855Z
close_reason: null
resolution: null
duplicate_of: null
---
`tests/test_resource_event_extract.py:665` is named
`test_write_resource_artifacts_atomically_replaces` and does not test atomicity. It
writes the artifacts twice and asserts `second_size >= first_size`. That assertion
passes against a plainly non-atomic implementation, because it never observes the
destination mid-write.

`filesystem-rules` names this exact anti-test:

> A test asserting `old == content || new == content` does not prove atomicity — it
> passes against a non-atomic implementation whenever the race does not happen to
> occur. Inject a failure before the commit point and assert two things: observers
> still see the original destination, and no success was reported.

Replace it with a failure-injection test:

1. Write the artifacts once and record the destination's bytes.
2. Write again with the producer raising partway through, before the commit point.
3. Assert the destination still holds the first write's exact bytes.
4. Assert the call reported failure rather than success.
5. Assert no `.partial` staging file is left behind at the destination.

`tests/test_runpool_status.py:143` (`test_atomic_write_creates_parents`) has the same
shape of name: it proves parent creation, which is worth testing, but it is not an
atomicity test either. Either rename it or give it the injection assertions too.

This bead is the probe fixture for the atomic-write gate: a rule whose tests cannot
demonstrate a rejection is indistinguishable from a rule that is off.
