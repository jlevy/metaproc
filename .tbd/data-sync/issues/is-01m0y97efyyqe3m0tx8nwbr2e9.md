---
type: is
id: is-01m0y97efyyqe3m0tx8nwbr2e9
title: Restore missing extra-plugin E2E fixture
kind: bug
status: open
priority: 1
version: 2
labels:
  - testing
  - metabrowser
dependencies: []
created_at: 2026-08-26T05:38:29.501Z
updated_at: 2026-09-01T06:10:17.899Z
---
tests/test_metabrowser_plugin_e2e.py skips test_extra_plugins_dir_is_loaded because tests/fixtures/sample_plugin/index.js is absent. Add a minimal generic fixture or replace the test with an equivalent temporary fixture, remove the skip path, and rerun browser/plugin checks. Keep live trace and live GCP skips as explicit environment-gated tests.

## Notes

Answered while migrating to browser SDK 0.5 (mp-g7l4): metabrowser 0.9.0 does NOT supply
this fixture, so the upgrade does not retire these beads.

Metabrowser does have a `sample_plugin`, but only at `tests/fixtures/sample_plugin` inside
its own repository, which the wheel does not ship. The skip at
`tests/test_metabrowser_plugin_e2e.py:189` guards on
`tests/fixtures/sample_plugin/index.js` under *this* repository, and that path has never
existed here. The fixture is this repository's to write.

What it needs is small: a directory under `tests/fixtures/sample_plugin` with a
`manifest.toml` declaring at least one kind and view, and an `index.js` calling
`registerView`, sufficient to exercise the extra-plugins-dir discovery path the loader
shim already supports through its `extraDirs` argument. Metabrowser's own copy is a
reasonable model to read while writing one.

Note for whoever takes this: the shim now evaluates an ES module entry point as well as a
classic script, so the fixture may be written in either shape, and writing it as ESM would
also cover that path for an external plugin.
