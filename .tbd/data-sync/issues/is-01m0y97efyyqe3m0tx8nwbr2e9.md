---
type: is
id: is-01m0y97efyyqe3m0tx8nwbr2e9
title: Restore missing extra-plugin E2E fixture
kind: bug
status: open
priority: 1
version: 1
labels:
  - testing
  - metabrowser
dependencies: []
created_at: 2026-08-26T05:38:29.501Z
updated_at: 2026-08-26T05:38:29.501Z
---
tests/test_metabrowser_plugin_e2e.py skips test_extra_plugins_dir_is_loaded because tests/fixtures/sample_plugin/index.js is absent. Add a minimal generic fixture or replace the test with an equivalent temporary fixture, remove the skip path, and rerun browser/plugin checks. Keep live trace and live GCP skips as explicit environment-gated tests.
