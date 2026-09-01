---
type: is
id: is-01m1dbd5pmnwbdzqg3tq72ma34
title: Bring first-party dependencies to their current releases
kind: task
status: closed
priority: 1
version: 2
labels:
  - supply-chain
  - release-blocker
dependencies: []
parent_id: is-01m1dbcer80nak10tnbg1jyq52
created_at: 2026-09-01T02:05:10.739Z
updated_at: 2026-09-01T05:23:24.708Z
closed_at: 2026-09-01T05:23:24.706Z
close_reason: "softschema was already on the current 0.8.0; only its audited rationale was stale and is now rewritten against 0.7.0. get-tbd moved 0.8.0 to 0.8.1 through tbd setup --auto, refreshing the hooks, skill files, and tbd_fallback_version together, with the exception rewritten against 0.8.0. frontmatter-format, flowmark-rs, and simple-modern-uv were already current. metabrowser and kpress are carved out to mp-g7l4: 0.9.0 is an SDK migration, not a bump, and it is the optional browser extra. SUPPLY-CHAIN-SECURITY.md now states the standing first-party currency rule outright."
resolution: null
duplicate_of: null
---
SUPPLY-CHAIN-SECURITY.md now states the standing rule outright: first-party libraries
track their latest release, because the cool-off buys no safety on code published from a
repository maintained alongside this one, and an entry naming an older release is drift.

Three first-party pins violate that rule as written:

- `metabrowser==0.1.0` in pyproject, current release 0.9.0. Pinned exactly, used by the
  development and plugin test group. This is the large one, and it is very likely the
  reason `tests/test_metabrowser_plugin_e2e.py:187` is permanently skipped for a missing
  `sample_plugin` fixture (mp-vtpx, mp-ugus) - check whether bringing it current
  restores that fixture and retires both beads.
- `kpress==0.2.2`, current release 0.3.5. Pulled by the exact Metabrowser development
  dependency, so it likely moves with metabrowser rather than on its own.
- `get-tbd==0.8.0` in the audited exception list and in `tbd_fallback_version` in
  `.tbd/config.yml`, current release 0.8.1, which is what this checkout already runs.
  The recorded fallback and the audited entry both name a release nobody is using.

Already current, no action: `softschema==0.8.0`, `frontmatter-format==0.4.0`,
`flowmark-rs==0.3.2`, `simple-modern-uv==v0.5.0`.

For each package that moves: bump the pin, refresh `uv.toml` exclude-newer where it
carries a package entry, regenerate `uv.lock` and `package-lock.json` as applicable,
rewrite the audited rationale against the release actually being replaced rather than
editing a version number, and run the full gate. Refresh the tbd fallback through
`tbd setup --auto` rather than hand-editing, per the existing note in that document.
