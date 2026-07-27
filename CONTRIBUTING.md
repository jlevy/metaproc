# Contributing

Contributions are welcome through GitHub issues and pull requests.
Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before You Start

For a substantial change, open an issue describing the user problem, proposed contract,
and compatibility impact.
Security reports follow [SECURITY.md](SECURITY.md), not the public issue tracker.

## Development Workflow

1. Fork and clone the repository.
2. Create a focused branch from current `main`.
3. Install the prerequisites in [development](docs/development.md): uv and the Node
   version pinned for your version manager.
4. Run `make install`.
5. Add or update tests with the implementation.
6. Run `make format` and `make verify`.
7. Review the diff and built artifacts for unrelated files or private data.
8. Open a pull request with the problem, approach, validation, and compatibility notes.

The project uses uv exclusively.
Do not add requirements files or instructions that invoke raw `pip` or an activated
virtual environment.

## Scope and Compatibility

Keep core generic. Consumer-specific process specs, schemas, handlers, commands,
fixtures, and configuration belong in downstream packages.
Add browser views through the documented Metabrowser plugin boundary instead of private
browser state.

Avoid breaking public CLI flags, process-file fields, runtime artifacts, Python plugin
types, manifest fields, or Agent Skill behavior without an explicit migration plan and
release note.

## Documentation and Tests

Apply `tbd guidelines common-doc-guidelines` when creating or restructuring
documentation, and format Markdown with Flowmark through `make format`. Documentation
must be safe for a public repository and must not contain credentials, private paths,
private issue IDs, or copied operational data.

Choose the narrowest test layer that proves the behavior.
See [testing architecture](docs/arch/arch-testing.md) for the suite structure.

By contributing, you agree that your contribution is licensed under the repository’s
[GNU Affero General Public License v3.0 or later](LICENSE).

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
