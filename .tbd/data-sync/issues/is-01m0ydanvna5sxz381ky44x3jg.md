---
type: is
id: is-01m0ydanvna5sxz381ky44x3jg
title: Define one serializable partial-resolution plan document
kind: feature
status: open
priority: 1
version: 1
labels:
  - architecture
  - planning
  - serialization
dependencies: []
created_at: 2026-08-26T06:50:09.643Z
updated_at: 2026-08-26T06:50:09.643Z
---
Design and architecture-review a versioned recursive PlanDocument that preserves authored expressions and stable node coordinates while representing symbolic, partially resolved, resolved, and invalid frontiers in one deterministic serialization. Keep scheduler attempts, commits, and expansions as durable facts; derive document state from those facts and current bindings. PlanDocument must replace overlapping plan serialization semantics, not become a mutable scheduler authority. Specify golden round-trips, monotonic resolution, dynamic fan-out states, frozen-source replay, renderer parity, compatibility, and secret/reference hygiene before implementation.
