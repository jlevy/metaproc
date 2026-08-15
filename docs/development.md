# Development

The essentials a developer needs to start working on metaproc itself.
For details about running workflows built on metaproc (research, GCP dispatch,
credentials), see
[environment-bootstrap.runbook.md](runbooks/environment-bootstrap.runbook.md).

## What Metaproc Is

A generic process framework for running structured multi-step agent workflows.
Source lives under [`src/metaproc/`](../src/metaproc/); the primary architecture
reference is [arch-metaproc-core.md](arch/arch-metaproc-core.md).

Metaproc is intentionally a thin layer above expensive things: agent SDKs, big JSONL log
streams, GCP Batch, and large repository trees.
Most performance and correctness bugs live at boundaries (file I/O, parser, adapter), so
the source is organized that way.

## Quickstart

```shell
make install
make format
make lint-check
make test
make verify
```

`make verify` is the handoff gate.
It installs both committed locks, checks formatting, lint, types, public hygiene, tests,
and dependency audits, builds both distributions, inspects their contents, and exercises
the installed wheel.
Run `make hooks-install` once per checkout to install the same pre-commit and pre-push
checks.

## Code Layout

| Path | Owns |
| --- | --- |
| [`src/metaproc/cli.py`](../src/metaproc/cli.py) | Top-level Typer CLI; commands register via `@app.command()`. |
| [`src/metaproc/commands/`](../src/metaproc/commands/) | One module per `metaproc <verb>` subcommand. |
| [`src/metaproc/engine/`](../src/metaproc/engine/) | Plan resolution, run-status scanning, retries, write-boundary checks. |
| [`src/metaproc/runpool/`](../src/metaproc/runpool/) | Adaptive concurrency controller and event log. |
| [`src/metaproc/adapters/`](../src/metaproc/adapters/) | One module per agent adapter (Claude Code, Gemini, Pi, Codex). |
| [`src/metaproc/logutil/`](../src/metaproc/logutil/) | JSONL log parsing and usage extraction; the shared parser layer. |
| [`src/metaproc/metabrowser_plugin/`](../src/metaproc/metabrowser_plugin/) | Metaproc-owned MetaBrowser file kinds, visualizations, log adapters, and data hooks. |
| [`src/metaproc/viz/`](../src/metaproc/viz/) | Process-spec visualization (DAG and render targets). |
| [`src/metaproc/cloud/gcp/`](../src/metaproc/cloud/gcp/) | GCP Batch dispatch and worker bootstrap. |
| [`src/metaproc/io/`](../src/metaproc/io/) | Frontmatter, schema tokens, lock files, dispatch manifests. |
| [`src/metaproc/models/`](../src/metaproc/models/) | Pydantic schemas for every cross-module data contract. |
| [`src/metaproc/stats/`](../src/metaproc/stats/) | Run-stats engine consumed by `metaproc stats` and the browser. |
| [`examples/`](../examples/) | Deterministic source-checkout examples, including the offline execution smoke. |
| [`tests/`](../tests/) | Unit, integration, cloud, golden, package, and browser-plugin tests. |
| [`devtools/`](../devtools/) | Lint, hygiene, supply-chain, and distribution checks plus the pinned-toolchain session bootstrap; not packaged. |

The browser is a standalone package with its own
[public architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md).
Metaproc owns the [domain plugin](../src/metaproc/metabrowser_plugin/README.md) and
retains historical framework [performance notes](performance-notes.md).

## Conventions

- **Naming, structure, and file-format rules:** [conventions.md](conventions.md).
  Read this before adding a new module, command, or env var.
  Format-selection rules live in
  [§File Format Policy](conventions.md#file-format-policy).
- **Runtime artifacts:** [artifact-catalog.md](artifact-catalog.md) lists every file
  metaproc writes or reads, with format, schema, lifecycle, and writer/readers.
- **No engine → viz coupling.** The projection layer is pure on `metaproc.models`; the
  engine never imports `metaproc.viz`.
- **Pydantic at every cross-module boundary.** If two modules talk via a dict, lift it
  to a model in `metaproc.models`.
- **Never hand-write Markdown.** Structured data lives in YAML frontmatter (a Pydantic /
  softschema model); the Markdown body is presentation rendered from that data.
  Render tables through the shared Markdown-table utility; never assemble pipes,
  separator rows, or escaped cells inline, and never parse structured state back out of
  body text. This is the repo-wide rule in [AGENTS.md](../AGENTS.md); document templates
  follow [conventions.md](conventions.md) § Template files and format status (one
  `*.template.md` suffix, `{{ }}` placeholders, `template.status` ladder).
- **Schema tokens version every persisted artifact.** See
  [conventions.md](conventions.md) §schema-tokens.
- **One bead per significant change.** [`tbd`](https://github.com/jlevy/tbd) is the
  issue tracker; agents create beads, not the human.

## Testing

- `make test` runs the full suite.
  Pytest discovers from `tests/`.
- Smaller per-module runs:
  `uv --config-file uv.toml run --frozen pytest tests/test_<module>.py -q`.
- Golden tests live under `tests/golden/`; regenerate with the snapshot helpers
  documented in [arch-testing.md](arch/arch-testing.md).
- Live integration tests under `tests/integration/` and `tests/cloud/` require their
  documented credentials or infrastructure and otherwise skip.

The Metabrowser plugin is checked JavaScript validated by Biome and TypeScript.
Biome does not provide static floating-promise analysis for JavaScript, so this project
does not yet claim the shared promise-safety lint floor.
The typescript-eslint overlay is tracked by `mp-608l` and remains gated on a patched
dependency graph clearing the third-party supply-chain cool-off.

## Performance Discipline

When you change anything in `logutil/`, `engine/run_status`, `stats/`, or
`osutils/ignore_filter`, run the focused performance tests and verify the relevant
numbers in [performance-notes.md](performance-notes.md) still hold.
The public benchmark harness is tracked as post-preview work in [TODO.md](../TODO.md).
Browser-side performance work lives in the external
[metabrowser](https://github.com/jlevy/metabrowser) package and follows that repo’s
development docs.

The patterns that produced the current numbers (`metaprocPerf` instrumentation,
`os.scandir` over `Path.iterdir`, scoped activity discovery, single-pass JSONL, lazy
serialization) are written up in **[performance-notes.md](performance-notes.md)**. Read
that before optimizing; it explains *why* each pattern matters and points at the worked
examples.

## Agent Adapters

Adding a new adapter is a five-file pattern:

1. `src/metaproc/adapters/<name>.py`: adapter class implementing `BaseAdapter`.
2. `src/metaproc/logutil/parsing.py`: register a `<Name>LogParser`.
3. `src/metaproc/data/pi-models.default.json`: pricing rows for any models the adapter
   exposes.
4. `tests/test_adapters_<name>.py`: happy path and failure modes.
5. `tests/test_log_parsing.py`: adapter detection and event shape.

See [credential-setup.runbook.md](runbooks/credential-setup.runbook.md) for credential
resolution and [arch-metaproc-core.md §12](arch/arch-metaproc-core.md) for the adapter
contract.

## Cloud (GCP) Development

Most metaproc development is local.
When you do need GCP:

- Container image: see [arch-cloud-execution.md](arch/arch-cloud-execution.md) for the
  rebuild path.
- Dispatch contracts: see
  [cloud-dispatch.runbook.md](runbooks/cloud-dispatch.runbook.md).
- Credentials and adapter auth: see
  [credential-setup.runbook.md](runbooks/credential-setup.runbook.md).
- End-to-end run setup (gcloud install, preflight, real mine command): see
  [environment-bootstrap.runbook.md](runbooks/environment-bootstrap.runbook.md).

`metaproc/` Python is baked into the agent image at build time; cloud runs do **not**
pick up branch edits automatically.
Ship code with the `METAPROC_WHEEL_GCS` and `METAPROC_WHEEL_SHA256` pair (fast), or
rebuild the image when an edit should become the default for every dispatch.
See
[Required Configuration](runbooks/cloud-dispatch.runbook.md#2-required-configuration)
for details.

## Architecture Docs

All architecture docs live in `docs/arch/` and follow the `arch-*.md` naming convention.
They carry frontmatter (title, description, author, status), a Date line with
`(last updated ...)`, a Maintenance blockquote pointing at
`tbd shortcut revise-architecture-doc`, and current implementation evidence.
The revision workflow verifies the document against code and records applicable open
questions and potential improvements.
To revise one:

```bash
tbd shortcut revise-architecture-doc
```

Keep this maintained index in sync with the files on disk.

| Doc | Owns | Status |
| --- | --- | --- |
| [arch-metaproc-core.md](arch/arch-metaproc-core.md) | The framework itself: spec format, runtime artifacts, CLI commands, adapter wire formats, and plugin protocol. Cross-references RunPool and cloud execution, which have their own documents. | Approved |
| [arch-runpool.md](arch/arch-runpool.md) | Local agent process manager: subprocess lifecycle, adaptive concurrency, host coordination, health telemetry, kill protocol. | Approved |
| [arch-cloud-execution.md](arch/arch-cloud-execution.md) | GCP Batch dispatch, container bootstrap, worker entrypoints, cross-host coordination, secret handling. | Approved |
| [arch-authentication.md](arch/arch-authentication.md) | Credential vehicles (A and B), per-attempt slot lifecycle, the auth-pool, cross-account leakage prevention. | Draft (currency notice) |
| [arch-claude-code-harness.md](arch/arch-claude-code-harness.md) | The non-interactive Claude Code CLI subprocess wrapper: environment scoping, settings hierarchy, permission mode and `ENV_SCRUB` hardening, and the version compatibility matrix. | Approved |
| [arch-testing.md](arch/arch-testing.md) | Test tiers (smoke-core, smoke-adapter-*, smoke-adapters-all, self-test-local, self-test-cloud) and the process specs that implement them. | Approved |
| [arch-file-io-utilities.md](arch/arch-file-io-utilities.md) | The `metaproc.io` public surface: atomic writes, gzip-transparent reads, frontmatter helpers, templates, and artifact paths. | Approved |

Companion conceptual and reference docs that are not architecture but are commonly
cross-linked from the arch docs:

- [metaproc-concepts-and-principles.md](../src/metaproc/docs/metaproc-concepts-and-principles.md):
  the conceptual model (vocabulary, architectural planes, optimization loops, design
  principles). The arch docs operationalize these concepts.
- [conventions.md](conventions.md): naming, structure, and file-format rules.
- [artifact-catalog.md](artifact-catalog.md): every file Metaproc writes and reads.
- [performance-notes.md](performance-notes.md): performance principles and worked
  examples.

## Cross-References

- [README.md](../README.md): repository entry point with the full doc index.
- [MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md)
  describes the standalone browser design and package boundary.
- [Metaproc MetaBrowser plugin](../src/metaproc/metabrowser_plugin/README.md):
  Metaproc-owned views, data hooks, assets, and validation.
- [environment-bootstrap.runbook.md](runbooks/environment-bootstrap.runbook.md):
  end-to-end setup for running real metaproc workloads.
- [credential-setup.runbook.md](runbooks/credential-setup.runbook.md): adapter
  credential configuration.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
