# Metaproc MetaBrowser Plugin

This package supplies Metaproc-specific file classification, visualizations, log
adapters, and server data hooks for [MetaBrowser](https://github.com/jlevy/metabrowser).
It keeps run and process concepts out of the generic browser while preserving rich views
when `metaproc` is installed.

The plugin is a subpackage of `metaproc`, not a separate distribution.
The Metaproc wheel contains its manifest, JavaScript, and CSS, and registers a callable
factory in the `metabrowser.plugins` entry-point group.

## Capabilities

The manifest claims five Metaproc-owned file kinds at higher priority than MetaBrowser’s
generic fallbacks:

| File kind | Views |
| --- | --- |
| `process-spec` | Visual DAG, rendered document, source, and steps |
| `resource-report` | Resource table, treemap, and raw JSON |
| `runpool-log` | Charts, statistics, normalized log, and raw JSON |
| `process-log` | Normalized log and raw JSON |
| `structure-report` | Summary, artifacts, graph, and source |

The plugin also provides:

- data hooks under `/api/plugin/metaproc/<route>` for process visualization, charts,
  statistics, resource snapshots, structure reports, and diagnostics
- `runpool` and `process` JSONL adapters registered through MetaBrowser’s public adapter
  registry
- renderer lifecycle cleanup that prevents stale asynchronous responses from updating a
  replaced preview and disposes chart resources

Without this plugin, MetaBrowser handles these files through its generic Markdown, JSON,
or JSONL fallbacks.

## Discovery and Public APIs

`metaproc/pyproject.toml` registers the plugin factory:

```toml
[project.entry-points."metabrowser.plugins"]
metaproc = "metaproc.metabrowser_plugin:plugin_dir"
```

`plugin_dir()` returns the packaged `plugin/` directory as a `Path`. This callable
contract works from source and installed wheels; consumers must not assume a
repository-relative asset path.

Python sidekicks import server helpers from the public `metabrowser` package root.
Browser code uses the documented `window.metabrowser` SDK, including
`fetchPluginData(...)` and `openPath(...)`. See the public
[plugin authoring guide](https://github.com/jlevy/metabrowser/blob/main/docs/plugins.md)
for the supported manifest, Python, and browser contracts.

The repository resolves the published `metabrowser==0.1.0` package from PyPI. Package
policy rejects workspace source overrides and restoration of an in-tree MetaBrowser
package.

The plugin owns its ELK.js graph-layout dependency.
It ships the audited `elkjs@0.10.0` browser bundle and EPL-2.0 license in the Metaproc
wheel, then loads the bundle before `viz.js`. This keeps process-visualization code out
of generic MetaBrowser and makes the renderer work without a CDN dependency.
The vendored bundle’s SHA-256 digest is
`48d338d5aeddd9503ccf1d12661c11b5d7d43c6afc5f66c7ddb2ea4170c0f6bf`.

From the consumer workspace, inspect the discovered plugin with:

```shell
uv sync --all-packages --all-groups --locked
uv run --package metaproc metab plugins show metaproc
uv run --package metaproc metab plugins doctor
```

## Package Layout

```text
src/metaproc/metabrowser_plugin/
├── __init__.py          # callable plugin factory and adapter registration
├── charts.py            # cached runpool and agent-log chart extraction
├── log_adapters.py      # runpool and process JSONL adapter bridges
├── sidekick.py          # server data-hook handlers
└── plugin/
    ├── manifest.toml    # file kinds, views, data hooks, and extra assets
    ├── index.js         # view registration and lifecycle ownership
    ├── styles.css       # plugin shell styling
    ├── domain_views.js  # process, resource, chart, and structure views
    ├── domain_views.css
    ├── elk.bundled.js   # vendored elkjs@0.10.0 graph-layout runtime
    ├── elkjs-license.txt
    ├── viz.js           # process DAG renderer
    └── viz.css
```

## Validation

The focused test boundary includes:

- [package contract](../../../tests/test_metabrowser_plugin_package_contract.py) for
  entry-point loading, declared assets, and public-only Python imports
- [wheel contents](../../../tests/test_metabrowser_plugin_wheel.py) for unique archive
  members and the exact packaged asset set
- [browser contract](../../../tests/test_metabrowser_plugin_e2e.py) and
  [rendering behavior](../../../tests/test_metabrowser_plugin_render.py) against
  installed MetaBrowser resources
- [renderer lifecycle](../../../tests/test_metabrowser_plugin_lifecycle.py) for stale
  response suppression and chart disposal
- integration coverage under the other `test_metabrowser_*.py` modules for file kinds,
  server hooks, KPress rendering, charts, visualizations, and CSS contracts

An isolated wheel environment containing released MetaBrowser and the historical
implementation plugin wheels must report every discovered plugin as healthy through
`metab plugins doctor`.

The consumer repository also owns the browser-asset quality gate after extraction.
`npm run check:metaproc-browser` runs Biome and TypeScript `checkJs` over the packaged
plugin and its DOM test harness.

The completed
[standalone extraction plan](../../../docs/project/provenance/extraction.md) records the
publication evidence and final deletion checklist.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
