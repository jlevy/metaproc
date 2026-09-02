# Development

The essentials a developer needs to start working on metaproc itself.
For details about running workflows built on metaproc (research, GCP dispatch,
credentials), see
[environment-bootstrap.runbook.md](runbooks/environment-bootstrap.runbook.md).

## What Metaproc Is

A generic process framework for running structured multi-step agent workflows.
Source lives under [`src/metaproc/`](../src/metaproc/); the primary architecture
reference is [metaproc-design.md](../src/metaproc/docs/metaproc-design.md).

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

### Public Hygiene

[`devtools/public_hygiene.py`](../devtools/public_hygiene.py) scans repository files,
built archives, and reachable Git metadata for material that should not appear in a
public repository: private names and paths, copied issue identifiers, personal email
addresses, and credentials.
It owns the exact patterns; two deliberate allowances are worth knowing before you read
them, because both look like rule violations and are not.

Git metadata is held to a looser rule than repository content.
A merge commit naming a numbered pull request, and a branch named after one, are
ordinary public convention rather than references to a private tracker, so pull-request
numbers are accepted in commit text and ref names alike.
The same number written into a document is still rejected, because there it can name a
tracker nobody outside the project can read — which is why this paragraph describes the
two examples instead of spelling them.
Ref names carry a second reason: the scan reads every local branch, so a stricter rule
would fail the gate on one contributor’s checkout over a name that was never pushed.

Commit attribution is normalized before scanning, so a `Co-authored-by` trailer does not
read as a personal email address.
An address in a commit *body* still does.

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

## The safeproc Workspace Member

`packages/safeproc` is a separate distribution incubating in this repository as a uv
workspace member: process-tree monitoring and host-safety coordination, with no runtime
dependencies and no imports from Metaproc.
It shares the root lockfile and supply-chain policy but keeps its own strict typing,
lint rules, tests, and source-free build.

```shell
make safeproc-format
make safeproc-lint-check
make safeproc-test
make safeproc-build
```

`make verify` includes all of them; ordinary Metaproc edit loops may skip them.
The package README and `packages/safeproc/docs/architecture.md` describe what exists,
and the
[incubation plan](project/specs/active/plan-2026-09-01-safeproc-local-incubation.md)
owns its phases and extraction gates.
Metaproc does not depend on it at runtime, and no release of it is published from this
repository.

## Conventions

- **Naming, structure, and file-format rules:**
  [conventions.md](../src/metaproc/docs/conventions.md).
  Read this before adding a new module, command, or env var.
  Format-selection rules live in
  [§File Format Policy](../src/metaproc/docs/conventions.md#file-format-policy).
- **Runtime artifacts:** [artifact-catalog.md](../src/metaproc/docs/artifact-catalog.md)
  lists every file metaproc writes or reads, with format, schema, lifecycle, and
  writer/readers.
- **No engine → viz coupling.** The projection layer is pure on `metaproc.models`; the
  engine never imports `metaproc.viz`.
- **Pydantic at every cross-module boundary.** If two modules talk via a dict, lift it
  to a model in `metaproc.models`.
- **Never hand-write Markdown.** Structured data lives in YAML frontmatter (a Pydantic /
  softschema model); the Markdown body is presentation rendered from that data.
  Render tables through the shared Markdown-table utility; never assemble pipes,
  separator rows, or escaped cells inline, and never parse structured state back out of
  body text. This is the repo-wide rule in [AGENTS.md](../AGENTS.md); document templates
  follow [conventions.md](../src/metaproc/docs/conventions.md) § Template files and
  format status (one `*.template.md` suffix, `{{ }}` placeholders, `template.status`
  ladder).
- **Schema tokens version every persisted artifact.** See
  [conventions.md](../src/metaproc/docs/conventions.md) §schema-tokens.
- **One bead per significant change.** [`tbd`](https://github.com/jlevy/tbd) is the
  issue tracker; agents create beads, not the human.

## Testing

- `make test` runs the full suite.
  Pytest discovers from `tests/`.
- Smaller per-module runs:
  `uv --config-file uv.toml run --frozen pytest tests/test_<module>.py -q`.
- Golden tests live under `tests/golden/`; regenerate with the snapshot helpers
  documented in [arch-testing.md](../src/metaproc/docs/arch-testing.md).
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

See [credential-setup.runbook.md](../src/metaproc/docs/credential-setup.runbook.md) for
credential resolution and
[metaproc-design.md §12](../src/metaproc/docs/metaproc-design.md) for the adapter
contract.

## Cloud (GCP) Development

Most metaproc development is local.
When you do need GCP:

- Container image: see
  [arch-cloud-execution.md](../src/metaproc/docs/arch-cloud-execution.md) for the
  rebuild path.
- Dispatch contracts: see
  [cloud-dispatch.runbook.md](../src/metaproc/docs/cloud-dispatch.runbook.md).
- Credentials and adapter auth: see
  [credential-setup.runbook.md](../src/metaproc/docs/credential-setup.runbook.md).
- End-to-end run setup (gcloud install, preflight, real mine command): see
  [environment-bootstrap.runbook.md](runbooks/environment-bootstrap.runbook.md).

`metaproc/` Python is baked into the agent image at build time; cloud runs do **not**
pick up branch edits automatically.
Ship code with the `METAPROC_WHEEL_GCS` and `METAPROC_WHEEL_SHA256` pair (fast), or
rebuild the image when an edit should become the default for every dispatch.
The wheel is installed by an already-running image entrypoint.
It therefore cannot replace that entrypoint’s own pre-bootstrap behavior during the same
process. Rebuild a candidate image for changes to `gcp_run_entrypoint.py`,
`worker_entrypoint.py`, `orchestrator_entrypoint.py`, secret hydration, or any code
needed before wheel installation.
See
[Required Configuration](../src/metaproc/docs/cloud-dispatch.runbook.md#2-required-configuration)
for details.

## Architecture Docs

The architecture documents ship inside the package, in `src/metaproc/docs/`, alongside
the design doc and the other framework documentation.
Each is also a `metaproc help` topic.
The indexed list, with topics and what each one owns, is
[README § Architecture](../README.md#architecture).

They follow the `arch-*.md` naming convention and carry frontmatter (title, description,
author, status) plus a Date line with `(last updated ...)`. To revise one:

```bash
tbd shortcut revise-architecture-doc
```

Two rules that shortcut does not know about, both because these documents ship:

- Future work goes in [`docs/project/design/backlog/`](project/design/backlog/), not
  into the document. A shipped document describes the system as it is.
- A relative link in `src/metaproc/docs/` must resolve inside that directory.
  Anything else is dead for a reader of the installed package even though
  `devtools/check_links.py` resolves it happily against a checkout.
  `devtools/check_shipped_links.py` enforces this.

See [docs/project/README.md](project/README.md) for the design records, revision
histories, and backlogs these documents were separated from.

## Cross-References

- [README.md](../README.md): repository entry point with the full doc index.
- [MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md)
  describes the standalone browser design and package boundary.
- [Metaproc MetaBrowser plugin](../src/metaproc/metabrowser_plugin/README.md):
  Metaproc-owned views, data hooks, assets, and validation.
- [environment-bootstrap.runbook.md](runbooks/environment-bootstrap.runbook.md):
  end-to-end setup for running real metaproc workloads.
- [credential-setup.runbook.md](../src/metaproc/docs/credential-setup.runbook.md):
  adapter credential configuration.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
