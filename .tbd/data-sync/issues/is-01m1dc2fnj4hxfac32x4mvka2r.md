---
type: is
id: is-01m1dc2fnj4hxfac32x4mvka2r
title: Migrate the Metabrowser plugin to browser SDK 0.5 (metabrowser 0.9.x)
kind: task
status: closed
priority: 2
version: 2
labels:
  - supply-chain
  - plugin
dependencies: []
created_at: 2026-09-01T02:16:49.061Z
updated_at: 2026-09-01T06:10:17.567Z
closed_at: 2026-09-01T06:10:17.566Z
close_reason: "Migrated in PR #60 (stacked on #59). metabrowser 0.1.0 to 0.9.0, kpress 0.2.2 to 0.3.5, manifest sdk_version 0.1 to 0.5. Three contract changes carried: mb.openPath to mb.navigation.open (removed with no shim at SDK 0.2), the markdown built-in's renderRendered to mountRendered (a live break, not a test artifact - the Document view would have registered undefined as its renderer), and selected-kind plugin asset loading at SDK 0.5, which needed no plugin change but invalidated the test asserting eager asset tags. The DOM shims now load the shell's own script order and evaluate ES modules via vm.SourceTextModule, since half of metabrowser's built-ins are ESM including the markdown one this plugin borrows from. make verify green: 4,571 passed, 8 skipped."
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
