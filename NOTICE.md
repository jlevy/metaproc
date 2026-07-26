# Metaproc Notices

Metaproc’s own license is AGPL-3.0-or-later, declared in `LICENSE`. This file records
the third-party component Metaproc vendors, under its own license and independently of
Metaproc’s license.

## Vendored Component

- **Eclipse Layout Kernel JavaScript** (`elkjs`) v0.10.0
  ([Eclipse Public License 2.0](src/metaproc/metabrowser_plugin/plugin/elkjs-license.txt),
  [ELK repository](https://github.com/kieler/elkjs)). The audited browser bundle is
  vendored as `src/metaproc/metabrowser_plugin/plugin/elk.bundled.js` for offline
  process-graph layout.

## Runtime Dependencies

Python dependencies are declared in `pyproject.toml`. They are installed under their own
licenses and are not redistributed by this package.

## Development Tooling

Development-only JavaScript dependencies are declared in `package.json`. They are
installed from the committed lockfile under their own licenses and are not redistributed
unless listed above as vendored components.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
