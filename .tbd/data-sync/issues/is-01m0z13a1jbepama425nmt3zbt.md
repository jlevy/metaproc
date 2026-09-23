---
type: is
id: is-01m0z13a1jbepama425nmt3zbt
title: Prevent staged cloud artifact identity reuse and overwrite
kind: bug
status: closed
priority: 0
version: 2
labels: []
dependencies: []
parent_id: is-01m0z0b4n9wnfj576rn1cpyfxr
created_at: 2026-08-26T12:35:39.697Z
updated_at: 2026-08-26T12:42:57.491Z
closed_at: 2026-08-26T12:42:57.490Z
close_reason: "Independent precommit review findings fixed: artifact uploads are create-only with generation-conditional retry; both gcp stage and gcp run validate GCP-safe immutable identities before artifact work; maintained architecture/runbook/module/changelog inventories agree; and the bounded-shutdown regression now bounds both synchronization waits. Focused tests, Ruff, BasedPyright, public hygiene, and the full Metaproc verification pass."
resolution: null
duplicate_of: null
---
The new gcp stage surface accepts a reusable job name while wheel/workspace uploads target deterministic gcp-run/<id> objects without a create-only precondition. A repeated identity can replace bytes that a dispatched run still references; digest verification fails closed but availability and immutability are broken. Reject path-like IDs and make uploads generation-zero/create-only, with a regression proving an existing object cannot be overwritten.
