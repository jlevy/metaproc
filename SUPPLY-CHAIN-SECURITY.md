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

First-party libraries track their latest release.
The 14-day cool-off exists to let a compromised third-party publish be caught by someone
else before it reaches this build; that argument does not apply to code published from a
repository maintained alongside this one, where the diff is reviewable directly.
Holding a first-party dependency back therefore buys no supply-chain safety and costs
currency, so an exception names the current release rather than whichever one happened
to be current when the exception was written.

Each exception is still audited: an entry below records the release it was reviewed
against and what changed, and a version bump requires a fresh review, not an edited
version number.

Seven exact first-party releases are exempt from the ordinary cool-off for this release:

- `softschema==0.9.1`, adopted with the owner’s explicit first-party cool-off exemption
  on 2026-10-25. Reviewed against `0.8.0`: same MIT license, `jlevy/softschema` source
  repository, Python `>=3.11,<4.0` floor, and five direct dependencies.
  Its frontmatter-format floor rises to `>=0.5.1`; no transitive dependencies are added.
  Validation result constructors require explicit `execution` evidence, and model-only
  enforcement produces an advisory.
  Metaproc forwards both native layer records and warnings without constructing results
  or inventing execution evidence for retained reports.
  The release also hardens portable YAML/frontmatter input and adds guarded JSON and
  C-parser paths with a pure-parser fallback.
  Its public `yaml_parser_mode()` reports the effective mode.
  The 0.9.1 patch restricts native parsing to the checked clib implementation, fixes
  `doctor` runner suggestions and excludes ruamel.yaml `0.19.0`; the lock keeps
  `0.19.1`. The validation-result API and bundled schemas are unchanged;
- `frontmatter-format==0.5.1`, adopted under the same owner exemption in the
  compatibility series required by Softschema 0.9.1. Reviewed against `0.4.0`: same MIT
  license, `jlevy/frontmatter-format` source repository, Python `>=3.10,<4.0` floor, and
  sole `ruamel-yaml>=0.18.10` dependency, now excluding `0.19.0`. The lock keeps
  `0.19.1`. The used APIs remain available.
  It adds guarded C-first readers, a pure-parser fallback and public
  `yaml_parser_mode()`, while keeping pure emitters and instance-local representers and
  constructors. The 0.5.1 patch adds unique atomic temporary writes with mode
  preservation, scalar escaping and a clib-specific native-parser gate.
  Both release wheels were non-yanked and matched their PyPI SHA-256 digests, and
  neither contains installation hooks.
  Their runtime modules match the published
  [Softschema](https://github.com/jlevy/softschema/tree/c01e5fd85aa51e7d3b32621c964fd8335ebf115c/packages/python/src/softschema)
  and
  [frontmatter-format](https://github.com/jlevy/frontmatter-format/tree/a9e062a1859dc4ad2a68cc8343ca3b8a6c40ca4c/src/frontmatter_format)
  release sources. Only these two package ceilings advance;
- `metabrowser==0.9.1`, the current release, used by the development and plugin test
  group and by the optional `browser` extra.
  Reviewed against `0.1.0`, the release it replaces here: same AGPL-3.0-or-later license
  and `jlevy/metabrowser` source repository, and relocking changed exactly two packages
  while adding and removing no transitive ones.
  Its Python floor rises from 3.11 to 3.12, which this project already requires.
  It advances the browser plugin SDK from 0.1 to 0.5, which is a contract change rather
  than a version bump: plugin discovery refuses a manifest targeting the wrong SDK, so
  this release was adopted together with the plugin migration that answers it.
  The 0.9.0-to-0.9.1 step within that adoption is a reviewed patch release carrying two
  correctness fixes — lazy plugin-view binding and schema-4 folder-rollup identity —
  with no license, source-repository, or dependency change and no Metaproc runtime or
  compatibility-layer change of its own;
- `kpress==0.3.5`, the current release, pinned exactly by `metabrowser==0.9.1` and so
  adopted with it rather than chosen separately.
  Reviewed as a compatible first-party maintenance update: same source repository and
  license, and no added dependencies;
- `flowmark-rs==0.3.2`, used to format and verify Markdown.
  This first-party release was reviewed against `0.3.1`; its formatting output is
  unchanged, while its skill, publishing, and Markdown-parser configuration are more
  reliable;
- `get-tbd==0.8.1`, the current release of the issue-tracking and agent-integration CLI,
  adopted inside the cool-off as a first-party release.
  Reviewed against the `0.8.0` recorded here previously: same MIT license, same
  `jlevy/tbd` source repository, same twelve direct dependencies, same npm publisher
  (GitHub Actions OIDC), and the same `f08` tbd format, so it carries no data migration.
  It has no lockfile effect, since the CLI is installed globally or run through `npx`
  rather than declared in `package.json`. Adopted through the supported
  `tbd setup --auto` path, which regenerates the hooks, skill files, `AGENTS.md` block,
  and the recorded fallback version together; the generated hooks read that one
  configured fallback rather than hardcoding a version in each script.
  The 0.8.0 entry this replaces recorded the f07-to-f08 format migration and the first
  release of the `agent-session-bootstrap` guideline that generalizes this repository’s
  own toolchain bootstrap; both remain in effect;
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
An entry naming a release older than the current first-party release is drift: bring the
pin forward and rewrite the rationale together, in one reviewed change.

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

## Agent CLI Pins

The four agent CLIs the adapters drive are third-party npm packages that Metaproc does
not install: no `package.json` or lockfile entry names them.
Each adapter pins one exact release in a `PINNED_*_VERSION` constant, and operators and
deployment images install that release.
A pin therefore changes which binary the adapter’s version check expects, not this
repository’s dependency closure, but it is held to the same 14-day cool-off.

The 2026-11-18 refresh moved Codex to `0.166.0` and Gemini CLI to `0.66.0`, each the
newest stable release outside the window; no advisory is fixed only in a later release
of either.

### Audited Third-Party Cool-Off Exceptions

Two third-party releases are adopted inside the cool-off by the owner’s explicit,
case-by-case exemption on 2026-11-18. The reason for both is model support, not an
advisory: Claude Fable 5.2 (`claude-fable-5-2`, released 2026-11-09) first appears in
Claude Code `2.1.321` and Pi `1.5.1`, both published that day.
Each exemption lapses when its release clears the window; a later version needs a fresh
review.

- **Claude Code `2.1.325`** (`@anthropic-ai/claude-code`, published 2026-11-13T20:07Z),
  replacing `2.1.234`; it clears the window on 2026-11-27. It is chosen over the
  `2.1.321` minimum because `2.1.323` removes long tool results
  `--no-session-persistence` left on disk and stops `--debug-file` replacing a `latest`
  file in the slot, and `2.1.325` fixes a security issue in zstd response decoding, a
  path every API response and `WebFetch` reach.
  `2.1.326` and `2.1.327` are not adopted.
  Reviewed against `2.1.234`:
  - Same package name and `SEE LICENSE IN README.md` license, with byte-identical README
    and LICENSE files.
  - Same seven-file wrapper and install model: a `postinstall` `install.cjs` copies the
    platform binary from an exact-version optional dependency over the `bin/claude.exe`
    placeholder. The script reads files and, on macOS only, runs `sysctl`; it fetches
    nothing.
  - No `dependencies`, the same `node >=22.0.0` floor, and no transitive growth.
  - The publisher changed: from `2.1.288` on, npm records GitHub Actions trusted
    publishing (OIDC) in place of a maintainer account.
  - Neither release carries an npm provenance attestation; both the wrapper and the
    `linux-x64` package pass `npm audit signatures`.
  - The `linux-x64` binary installed with `--ignore-scripts` reports `2.1.325` and its
    SHA-256 matches an independently downloaded tarball of the same version.
- **Pi `1.5.2`** (`@earendil-works/pi-coding-agent`, published 2026-11-10T12:24Z),
  replacing `0.84.2`; it clears the window on 2026-11-24. It is chosen over `1.5.1`
  because its shipped code differs only in an interactive prompt, an `/import` cleanup
  and the Claude Code version Anthropic OAuth requests report, with identical
  dependencies and built-in catalogs for every provider Metaproc uses.
  The npm advisory endpoint lists nothing against Pi’s packages at any of these
  versions. Reviewed against `0.84.2` and `1.5.0`:
  - Same MIT license, `earendil-works/pi` repository, maintainers and publisher (GitHub
    Actions trusted publishing).
  - SLSA v1 provenance names `build-binaries.yml@refs/tags/v1.5.2` at commit
    `b4afdddf44e059c67ccf5097e5317d913521b470`, which matches the tag; the attested
    SHA-512 matches the downloaded tarball, and the seven exact-pinned first-party
    `@earendil-works` packages carry provenance from the same tag.
  - The Pi packages define no install scripts; the transitive `@google/genai` (a no-op
    `preinstall`) and `protobufjs` (`postinstall`) are the same pair as `0.84.2`.
  - Pi has shipped no `npm-shrinkwrap.json` since `1.0.2`, so its third-party
    dependencies float and stay under the cool-off; only the `@earendil-works` scope is
    exempt. The adapter’s install hint keeps the gate:
    `npm install -g --ignore-scripts --min-release-age=14 '--min-release-age-exclude=@earendil-works/*' @earendil-works/pi-coding-agent@1.5.2`.
    That resolves 122 packages, every third-party one outside the window, and
    `npm audit` reports none; a `0.84.2` tree reported seven, two high.
  - Behavior changed since `0.84.2` is pinned back where Pi allows it: the adapter
    passes `--no-mcp` and sets `PI_TELEMETRY=0`, and the packaged
    `pi-models.default.json` turns `1.5.1`’s default Anthropic message threads off.

## Audited Advisory Waivers

An advisory is waived only when the vulnerable code path is unreachable from this
dependency closure and the fix is not yet installable under the cool-off.
Severity alone does not decide it: a high-severity finding in an API nothing here calls
carries no exposure, while any finding in a reachable path is fixed rather than waived.
Waivers are per-ID, live in the `audit` target, and are removed as soon as the fix
becomes eligible.

No waiver is active.

The previous waiver, `GHSA-g6cj-pr64-35w5` / `CVE-2026-69247` (high, CVSS 8.2) in the
`cryptography` `pkcs7` `EnvelopedData` decryption path, was removed for the 0.3.0
release: its fix, `cryptography` 50.0.0, cleared the cool-off and is now locked.

The September 2026 `urllib3` security release is locked at `2.8.0`, replacing `2.7.0`
without an advisory waiver.
It fixes `GHSA-8988-9cw3-xx77`, `GHSA-vxq7-64xx-v4gw`, and `GHSA-gh4c-6fx4-qh6g`. The
[PyPI release](https://pypi.org/project/urllib3/2.8.0/) uploaded its wheel and source
distribution on September 15, more than 14 days before adoption; both carry
trusted-publishing attestations from `urllib3/urllib3`. The
[upstream release](https://github.com/urllib3/urllib3/releases/tag/2.8.0) has a signed,
immutable tag and documents the HTTPS proxy TLS behavior correction.
The package keeps the MIT license, Python `>=3.10` floor, and dependency metadata of
`2.7.0`; the lock change replaces only its version and two artifact records, with no
transitive growth.

`certifi` is locked at `2026.10.23`, replacing `2026.10.19`, without an advisory waiver.
`2026.10.19` fixed `GHSA-6q48-6c7w-9gw2` (critical): eight eMudhra intermediate CAs that
chain to the `emSign ECC Root CA - G3` root have compromised private keys, so a holder
of one can issue a certificate for any hostname, and an on-path attacker can then read
or modify TLS traffic from any client trusting the bundle.
`2026.10.23` keeps that fix and answers no advisory of its own.

Unlike the `urllib3` adoption above, `2026.10.19` was adopted *inside* the cool-off,
through a package-scoped `exclude-newer-package` pin in `uv.toml`. Waiting was not an
alternative to review there: every `certifi` release from `2019.03.09` carries the
affected root, so no older pin avoids the advisory, and the fix is reachable rather than
unreachable, which the waiver rule above resolves in favor of fixing.
Exposure is the `gcp` extras and the dev groups, which pull `certifi` through `requests`
and through `httpcore` and `httpx` respectively; the `browser` extra does not reach it,
and Metaproc’s core dependencies do not include it.

That pin recorded its own removal condition: the first ordinary relock after 2026-11-03,
when `2026.10.19` cleared the 14-day window on its own.
This is that relock, so the entry is gone and `certifi` resolves under the global
cool-off again. An `exclude-newer-package` entry is only a ceiling, so once the release
it admitted had cleared the global window the entry admitted nothing further and instead
held the trust store at `2026.10.19`, hiding `2026.10.23`.

`2026.10.23` was audited as a trust-store change rather than a code change, on the same
terms as the release it replaces: `core.py` is byte-identical to `2026.10.19`,
`__init__.py` differs only in its version string, and the two wheels otherwise carry the
same modules, so the substance is the PEM bundle.
It removes two more emSign roots, `emSign Root CA - C1` and `emSign ECC Root CA - C3`,
taking the bundle from 120 certificates to 118 and leaving `emSign Root CA - G1` as the
only emSign root still trusted; `2026.10.19` had restored all three of those after
`2026.10.18` removed every emSign root wholesale.
The bundle diff is 51 removed lines and no added line, so this relock trusts strictly
less than the pin it replaces, and the four Mozilla CA program roots that pin accepted
early (`SECOM TLS ECC Root CA 2024`, `SECOM TLS RSA Root CA 2024`,
`Telia EC TLS Root CA v3`, and `Telia RSA TLS Root CA v3`) are unchanged here.
The [PyPI release](https://pypi.org/project/certifi/2026.10.23/) uploaded its wheel and
source distribution on 2026-10-23, more than 14 days before adoption, so the ordinary
cool-off admits it; the wheel carries a trusted-publishing attestation from
`certifi/python-certifi` built by `release.yml`, and its locked SHA-256 matches PyPI.
The package keeps the MPL-2.0 license, the Python `>=3.7` floor, and the empty
dependency metadata of `2026.10.19`, so the lock change replaces only its version and
artifact records with no transitive growth.

`opentelemetry-api` is locked at `1.45.2`, replacing `1.45.1`, without an advisory
waiver. It fixes `GHSA-mf5v-w3w3-f6cg` / `CVE-2026-126360` (moderate, CVSS 5.3):
quadratic-time `traceparent` parsing, a denial of service against any caller that parses
an attacker-supplied trace header.
Every release before `1.45.2` carries it, `1.45.1` included, so the `1.45.1` entry this
replaces was accurate when written and went stale when the advisory was published on
2026-11-13; the fix it recorded, `GHSA-xcq7-p3pf-p88h` for unbounded `tracestate`
parsing, is untouched and remains in effect.
Metaproc does not import OpenTelemetry; it arrives transitively through the Google Cloud
client libraries of the `gcp` extras.
The [PyPI release](https://pypi.org/project/opentelemetry-api/1.45.2/) uploaded its
wheel and source distribution on 2026-10-29, more than 14 days before adoption, so the
ordinary cool-off admits it with a plain relock and no package-scoped pin; PyPI reports
no provenance bundle for the wheel, as for `1.45.1`. The package keeps the Apache-2.0
license, the Python `>=3.10` floor and the `typing-extensions>=4.5.0` dependency of
`1.45.1`, so the lock change replaces only its version and artifact records, with no
transitive growth.

`pyasn1` is locked at `0.6.5`, replacing `0.6.4`, without an advisory waiver.
It fixes `GHSA-9m75-vc3g-4x39`, `GHSA-mm57-4v3p-rchx`, `GHSA-fm9c-5rcf-2q5p`, and
`GHSA-2qm8-x737-gmw5` (each high, CVSS 7.5): quadratic-time decoding of constructed
strings and indefinite-length `ANY` values, binary `REAL` values, schemaless containers,
and constructed `BIT STRING` values.
Metaproc does not import `pyasn1`; it arrives through `pyasn1-modules`, which
`google-auth` requires for the `gcp` extras.
Its only importer in this closure is the `_python_rsa` fallback in `google-auth`, which
also needs the `rsa` package; the lock does not contain `rsa`, so nothing loads the
decoder. The [PyPI release](https://pypi.org/project/pyasn1/0.6.5/) uploaded its wheel
and source distribution on 2026-10-27, more than 14 days before adoption, so the
ordinary cool-off admits it; PyPI reports no provenance bundle for either file, as for
`0.6.4`. Both artifacts were compared with the unsigned upstream tag `v0.6.5` (commit
`5d16736`): the wheel’s 32 modules and 149 of the source distribution’s 156 files are
byte-identical to the tag, and the remaining files are setuptools-generated metadata.
The package keeps the BSD-2-Clause license, the Python `>=3.8` floor and the empty
dependency metadata of `0.6.4`, so the lock change replaces only its version and
artifact records, with no transitive growth.

Re-review a waiver whenever the closure changes such that the affected code could become
reachable.

## Native YAML Emitter Test Dependency

The isolated `make test-yaml-c-emitter` lane uses `ruamel.yaml.clib==0.2.15` to test the
optional C emitter and guarded reader that the ordinary installation does not provide.
This is the native implementation used by `ruamel.yaml`; no existing pure-Python
dependency can exercise that boundary.

The [PyPI release](https://pypi.org/project/ruamel.yaml.clib/0.2.15/) is maintained by
Anthon van der Neut, points to the
[ruamel-yaml-clib source](https://sourceforge.net/p/ruamel-yaml-clib/code/ci/default/tree/),
and carries the MIT license.
Its reviewed CPython 3.14 wheels for Linux x86_64 (`manylinux_2_17`/`manylinux_2_28`)
and macOS arm64 were uploaded on 2025-11-16, outside the 14-day cool-off.
Each contains the native `_ruamel_yaml` extension and distribution metadata, with no
Python dependencies or install scripts.

[`devtools/requirements-yaml-c-emitter.txt`](devtools/requirements-yaml-c-emitter.txt)
pins the version and the two PyPI SHA-256 wheel hashes.
The lane permits binary wheels only and requires matching hashes, so it cannot fall back
to an unreviewed source build or another platform’s artifact.
It installs into a separate target directory exposed only to these YAML tests, leaving
`uv.lock`, production dependencies, and the ordinary suite unchanged.
The lane fails if the extension or C emitter is unavailable; it tests the actual status,
scale-state, scale-override, and kill-sentinel writers.
It also checks frontmatter repair with the guarded reader in C-first and forced-pure
modes, preserving valid flow collections and repairing malformed scalars.
The supported frontmatter-format and Softschema releases guard their C-first readers and
fall back to pure parsing; see
[installation](docs/installation.md#optional-yaml-c-extension).

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
