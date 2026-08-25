# Agent Instructions

Start with [development](docs/development.md),
[supply-chain security](SUPPLY-CHAIN-SECURITY.md), and the documentation relevant to the
change. The full documentation map is in
[README.md § Documentation](README.md#documentation).

## Metaproc Self-Documentation

Metaproc documents itself.
Before running, monitoring, or debugging any metaproc process, read
`metaproc help operator`; `metaproc help developer` and `metaproc help concepts` cover
extension policy and the process model.
The generated Agent Skill in `.agents/skills/metaproc/` routes to the same manuals.
After changing the skill baseline, spec, or help topics, regenerate the committed copies
with `metaproc skill metaproc --install` (a drift test enforces this).

## Build and Test

Use the repository Make targets:

```shell
make install
make format
make lint-check
make test
make verify
```

`make verify` is the required handoff gate.
It includes Python and browser formatting checks, lint, type checks, public-hygiene
checks, tests, locked Python and npm vulnerability audits, distribution inspection, and
isolated installed-wheel smoke tests.
Run `make hooks-install` once per checkout to install the Lefthook pre-commit and
pre-push gates.

## Python and Dependencies

- Use uv exclusively. Never invoke raw `python` or `pip`, activate `.venv`, or add a
  second environment manager.
- Read [SUPPLY-CHAIN-SECURITY.md](SUPPLY-CHAIN-SECURITY.md) before any dependency or
  tool change.
- Preserve the 14-day cool-off and package-scoped first-party exceptions.
  Commit `uv.lock` and `package-lock.json` when their dependencies change.
- Support Python 3.12 through 3.14 and add complete annotations to changed code.
- Keep local planning and execution independent of optional GCP dependencies and
  credentials.
- Catch only errors the current layer can handle and preserve exception causes.

## Framework and Plugin Boundaries

- Keep Metaproc core consumer-agnostic.
  Domain process specs, schemas, handlers, fixtures, commands, and configuration belong
  in downstream packages.
- Keep cloud support behind optional extras and import guards.
- Preserve public CLI flags, process-file fields, runtime artifact shapes, plugin entry
  points, and Agent Skill behavior unless the change includes a migration plan.
- The Metabrowser plugin owns Metaproc-specific views and uses the public Metabrowser
  SDK. Do not reach into private browser globals.
- Run Biome and TypeScript checks through the Make targets after JavaScript, CSS, JSON,
  or plugin changes.

## Documentation and Public Hygiene

- Apply `tbd guidelines common-doc-guidelines` to every human-authored document and
  retain the standard footer.
- Format Markdown with the exact Flowmark pin through `make format`.
- Link to source documentation instead of duplicating long policy text.
- Never add credentials, private repository names, private issue IDs, personal absolute
  paths, customer data, or copied operational artifacts.
- Run the public-hygiene and distribution checks before every release or repository
  visibility change.

## Git

Keep changes focused and preserve unrelated work.
Before handoff: review the diff, run `make verify`, update and close the relevant tbd
issues, run `tbd sync`, commit, push, open or update the pull request, and watch CI to
completion.

<!-- BEGIN TBD INTEGRATION format=f08 surface=agents-md -->
## tbd

This repository uses **tbd** for git-native issue tracking (beads), spec-driven
planning, and on-demand engineering guidelines.
As the agent, you operate tbd on the user’s behalf: translate their requests into tbd
actions rather than telling them to run commands.

- Run `tbd prime` to load current project state and the full tbd workflow.
- Run `tbd skill` for the complete reusable tbd skill instructions.
- Run `tbd shortcut --list` and `tbd guidelines --list` for on-demand resources.
- Track all work as beads: `tbd create`, `tbd ready`, `tbd close`, and `tbd sync`.

<!-- END TBD INTEGRATION -->

## Template Maintenance

This project was built from
[simple-modern-uv](https://github.com/jlevy/simple-modern-uv).
Routine project work uses the instructions above; do not fetch the upstream template for
every task.

For toolchain changes, selective adoption of another template feature, or a Copier
update, use the portable
[simple-modern-uv skill](https://github.com/jlevy/simple-modern-uv/tree/main/skills/simple-modern-uv).
It preserves project-specific choices and distinguishes selective changes from full
template management.
`.copier-answers.yml` records this project’s update lineage.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
