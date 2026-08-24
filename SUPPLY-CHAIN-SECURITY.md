# Supply-Chain Security

Dependencies and build tools are code execution boundaries.
Review this document before adding, upgrading, or invoking a package.

## Required Defaults

- Use uv for Python resolution, execution, and tools.
  Do not use raw `pip` or an activated virtual environment.
- Apply a 14-day release cool-off with `uv.toml`, `UV_EXCLUDE_NEWER`, or the equivalent
  explicit uv flag.
- Commit `uv.lock`, install it with `uv --config-file uv.toml sync --locked` so lock
  drift fails the build, and execute commands with
  `uv --config-file uv.toml run --frozen` so they cannot resolve dependencies.
- Commit `package-lock.json`, use exact JavaScript tool versions, and install it with
  `npm ci`.
- Run locked JavaScript tools with `npx --no-install`; use exact versions for one-shot
  `uvx` tools.
- Disable npm lifecycle scripts unless a reviewed package specifically requires them.
- Keep npm’s release-age gate, exact-save behavior, and lockfile generation enabled.
- Use npm 11.10 or newer so `min-release-age` is enforced rather than ignored.
- Pin GitHub Actions to reviewed full commit SHAs.
- Pin the CI and publishing Node release instead of resolving a moving major version.
- Build without isolated dependency re-resolution and test the wheel in a clean
  environment.

## Review Before Adding

Confirm:

1. the dependency is necessary and existing code cannot provide the capability;
2. the package name, publisher, source repository, and license are correct;
3. the selected release is at least 14 days old;
4. the release artifacts and install behavior are expected;
5. transitive dependency growth is proportionate;
6. the lockfile change contains no unexplained package or source changes.

## Audited First-Party Exceptions

Seven exact first-party releases are exempt from the ordinary cool-off for this release:

- `softschema==0.4.0`, required for portable date- and timestamp-shaped YAML scalars;
- `frontmatter-format==0.4.0`, required by SoftSchema 0.4.0 and used directly for
  deterministic alias-free Metaproc artifact writes;
- `metabrowser==0.1.0`, used only by the development and plugin test group;
- `kpress==0.2.2`, pulled by the exact Metabrowser development dependency and reviewed
  as a compatible first-party maintenance update with no added dependencies;
- `flowmark-rs==0.3.2`, used to format and verify Markdown.
  This first-party release was reviewed against `0.3.1`; its formatting output is
  unchanged, while its skill, publishing, and Markdown-parser configuration are more
  reliable;
- `get-tbd==0.8.0`, the issue-tracking and agent-integration CLI, adopted inside the
  cool-off as a first-party release.
  Reviewed against the `0.6.5` this repository ran previously: same MIT license and
  `jlevy/tbd` source repository, same twelve direct dependencies, and no lockfile
  effect, since the CLI is installed globally or run through `npx` rather than declared
  in `package.json`. It migrated this repository’s tbd format from f07 to f08 through
  the supported `tbd setup --auto` path, regenerating the hooks, skill files, and
  `AGENTS.md` block together, and it is the first release to ship the
  `agent-session-bootstrap` guideline that generalizes this repository’s own toolchain
  bootstrap. The generated hooks read one configured fallback version rather than
  hardcoding it in each script;
- `simple-modern-uv==v0.5.0`, the Copier template this repository is generated from,
  applied inside the cool-off as a first-party release.
  Reviewed against the `v0.4.0` recorded previously in `.copier-answers.yml`: same MIT
  license and `jlevy/simple-modern-uv` source repository.
  It has no lockfile effect of its own — it is a template applied with `copier update`,
  not a declared dependency — and the update was run under the ordinary gate
  (`uvx --exclude-newer "14 days" copier@9.17.0`). Its rendered changes to this
  repository are reviewed in the diff rather than taken on trust: the project-owned
  `uv.toml` and `UV_CONFIG_FILE` selection, the pinned action and toolchain bumps below,
  and the dev-dependency floors it raises.

The exceptions are package-scoped in configuration and do not weaken the global gate.
Changing any version requires a new review and an updated rationale.

The generated agent-integration scripts read one exact release from
`tbd_fallback_version` in `.tbd/config.yml` rather than repeating it per script.
Hooks prefer an already-installed `tbd` binary and otherwise use that exact pinned npm
release as a zero-install fallback.
The session bootstrap also installs the pinned, checksum-verified `gh` 2.92.0 binary
into a user-local bin directory when `gh` is missing.
Refresh the generated hooks, skill files, and that fallback version together with
`tbd setup --auto`.

`devtools/ensure-toolchain.sh` does the same for the Node and uv toolchain itself, so an
agent session that starts from a bare container can run the Make targets.
Claude Code and Codex both invoke that one shared script at session start.
It installs the repository’s own pins, never the newest release: it resolves Node from
`.node-version` and uv from the `uv.toml` `required-version` range, verifies each
download against a checksum pinned in the script, and refuses a mismatch.
The pinned uv sits inside that range rather than on its floor: the range states which uv
line the committed lockfile is valid for, while the pin selects the newest release in
that line that has cleared the cool-off.
A newer Node major would carry an npm outside the `engines` range, which
`engine-strict=true` turns into a failed `npm ci`. Bump a pin in its canonical file and
in that script together; `check_supply_chain.py` fails when they disagree, or when
either agent stops running the bootstrap ahead of its other session hooks.
[Agent toolchain bootstrap](docs/agent-toolchain-bootstrap.md) records this repository’s
pins, wiring, and guard; `tbd guidelines agent-session-bootstrap` states the general
pattern, including when a provisioned image is the better answer.

## Audited Advisory Waivers

An advisory is waived only when the vulnerable code path is unreachable from this
dependency closure and the fix is not yet installable under the cool-off.
Severity alone does not decide it: a high-severity finding in an API nothing here calls
carries no exposure, while any finding in a reachable path is fixed rather than waived.
Waivers are per-ID, live in the `audit` target, and are removed as soon as the fix
becomes eligible.

One waiver is active:

- `GHSA-g6cj-pr64-35w5` / `CVE-2026-69247` (high, CVSS 8.2), a Bleichenbacher oracle in
  the `cryptography` `pkcs7` `EnvelopedData` decryption path.
  `cryptography` is an indirect dependency reached only through `google-auth` under the
  `gcp` extra. Neither Metaproc nor `google-auth` imports the affected `pkcs7` module;
  `google-auth` uses `cryptography` for JWT signing and verification only.
  The fix, `cryptography` 50.0.0, was published 2026-07-31 and is inside the 14-day
  cool-off until roughly 2026-08-14. Remove the waiver and relock once it is eligible.

Re-review a waiver whenever the closure changes such that the affected code could become
reachable.

## Verification

`devtools/check_supply_chain.py` checks only safeguards that span configuration files:
npm safety settings, exact direct npm specifications, npm registry and integrity data,
the uv cool-off, matching nvm and fnm versions, agreement between the toolchain
bootstrap’s pins and their canonical files, both agents running that bootstrap first,
each agent’s copy of a generated hook script matching its twin, full-SHA action
references, and trusted publishing controls.

The configuration files own dependency versions, build behavior, lint and type ratchets,
workflows, and documentation.
`make verify` installs both locks, runs the configured linters and type checkers,
executes the tests and audits, builds the package, inspects its contents, and exercises
the installed wheel, CLI, data, documentation, Agent Skill, and Metabrowser plugin
surface.

CI and publishing also run `npm audit --audit-level=moderate` after installing the exact
lock.

Treat an unexpected lockfile source, install script, binary artifact, or publish-time
change as a blocker until it is explained and reviewed.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
