---
type: is
id: is-01m0txvyg5304wg7ja18nfes0r
title: "Metabrowser extra-plugins-dir e2e test is permanently skipped: sample_plugin fixture absent"
kind: bug
status: open
priority: 2
version: 1
labels:
  - testing,metabrowser
dependencies: []
created_at: 2026-08-24T22:22:15.044Z
updated_at: 2026-08-24T22:22:15.044Z
---
## Finding

`tests/test_metabrowser_plugin_e2e.py::test_extra_plugins_dir_is_loaded` never executes, in any environment including CI. It self-skips at line 186-187:

```python
fixture_dir = Path(__file__).resolve().parent / "fixtures"
if not (fixture_dir / "sample_plugin" / "index.js").is_file():
    pytest.skip("sample_plugin fixture missing")
```

`tests/fixtures/sample_plugin/` does not exist. The directory contains auth_env, claude_api_signals, fingerprint_smoke, layout_smoke, log_compaction, pro_cap_exhaustion, replay_smoke, and trace_agents, and nothing else.

The test's own docstring says it "Uses the existing sample_plugin fixture which has its own registerView call", so the fixture was expected to be present. It was either never committed or removed without removing its consumer.

## Impact

The `METABROWSER_PLUGINS_DIRS` extra-plugins-dir discovery path has no end-to-end coverage: passing a plugin directory as an extra arg and having it go through discovery plus `index.js` load is asserted nowhere. AGENTS.md treats plugin entry points as a preserved public surface, so this path is supposed to be protected against regression and currently is not.

This is a green-looking skip rather than a failure, which is why it has gone unnoticed. It is one of 8 skips in the suite; the other 7 are legitimate opt-in gates (4 requiring live GCP credentials, 3 requiring `METAPROC_TRACE_SMOKE_RUN_DIR`).

## Action

Add `tests/fixtures/sample_plugin/index.js` with a minimal `registerView` call, matching what the test expects. Then convert the guard from a skip to a hard failure so an absent fixture cannot silently disable the test again. The `_has_node()` skip above it is a legitimate environment gate and should stay.

## Not a 0.3.0 blocker

Filed as ordinary follow-up. It is a pre-existing coverage gap, not a regression introduced since v0.2.1.
