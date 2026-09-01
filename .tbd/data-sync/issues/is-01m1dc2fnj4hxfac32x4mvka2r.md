---
type: is
id: is-01m1dc2fnj4hxfac32x4mvka2r
title: Migrate the Metabrowser plugin to browser SDK 0.5 (metabrowser 0.9.x)
kind: task
status: closed
priority: 2
version: 5
labels:
  - supply-chain
  - plugin
dependencies:
  - type: blocks
    target: is-01m1f8xgv4zycvj17qc7g9x1d0
parent_id: is-01m1f8xgv4zycvj17qc7g9x1d0
created_at: 2026-09-01T02:16:49.061Z
updated_at: 2026-09-01T20:20:43.715Z
closed_at: 2026-09-01T20:20:43.715Z
close_reason: "Metaproc PR #64 merged as 10f51859c6b09ca41cddb9384c7ee0f549de984f after the full local gate and all hosted CI passed; disposition comments were posted on PRs #59 and #60."
resolution: null
duplicate_of: null
---
Attempted as a version bump on 2026-08-31 and reverted: it is an SDK migration, and
Metabrowser says so itself. Discovery under 0.9.0 refuses the plugin outright:

  plugin 'metaproc' targets browser SDK '0.1', but this Metabrowser provides '0.5';
  update the plugin for the current SDK and set sdk_version accordingly

Evidence from the attempt, at metaproc main 72ae119 with `metabrowser==0.9.0` and
`kpress==0.3.5` locked:

- The dependency graph itself is clean. Relocking changed exactly two packages and added
  or removed no transitive ones, and metabrowser 0.9.0 pins `kpress==0.3.5`, so kpress
  moves with it rather than needing its own decision. Same AGPL license and source
  repository; the Python floor rises from 3.11 to 3.12, which this project already meets.
- 21 tests fail and 10 error, in three distinct groups.
- Manifest contract: `src/metaproc/metabrowser_plugin/plugin/manifest.toml` declares
  `sdk_version = "0.1"` against a provider now at 0.5. Every plugin-discovery and
  integration test fails on this one refusal.
- Browser asset rename: the SDK asset is `static/plugin-sdk.js`, not
  `static/plugin_sdk.js`; `tests/dom/render_metabrowser_view.js:111` loads it by the old
  name. `window.metabrowser` is still the global, so the plugin's own entry points
  (`plugin/index.js`, `plugin/domain_views.js`) are unaffected by this part.
- SDK load order: the new `plugin-sdk.js` throws "plugin SDK requires the canonical
  navigation module" when loaded standalone, so the DOM shim has to load the SDK's
  prerequisites in the order the real page does rather than loading two files.

Not a release blocker. `metabrowser` is the optional `browser` extra, so it gates no core
command and no wheel smoke path. It was deliberately left out of the v0.4.0 candidate
rather than rushed, because a partial plugin migration would enlarge the release delta in
the one area with no test coverage from the core suite.

Worth checking while doing it: whether SDK 0.5 restores the `sample_plugin` fixture whose
absence permanently skips `tests/test_metabrowser_plugin_e2e.py:187` (mp-vtpx, mp-ugus).

The audited exception entry in SUPPLY-CHAIN-SECURITY.md must be rewritten against 0.1.0
as part of this, not edited to a new version number.

## Notes

PR #60 merged the initial SDK 0.5 migration. Independent post-merge review R60-1 reproduced a real lazy-load failure: selecting a Metaproc-owned kind does not load foreign built-ins whose renderers it embeds. Follow-up on codex/downstream-safe-latest now awaits ensureKindAssets for both markdown (process-spec and structure-report views) and agent-log (runpool/process log views), with an isolated host test that begins with empty builtins, loads each dependency on demand, verifies registration, and exercises the delegated log renderers. Focused lifecycle, Biome, and TypeScript checks pass; the preceding full make verify was green before the agent-log extension and will be rerun on the final tree.
