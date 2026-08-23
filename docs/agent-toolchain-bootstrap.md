# Agent Toolchain Bootstrap

Metaproc pins an exact Node and uv, and agent sessions start from a bare container that
has neither. Until the toolchain is installed, every Make target, `uv` command, and
`npm ci` fails, so each session begins with the same manual setup.
`devtools/ensure-toolchain.sh` removes that step: Claude Code and Codex both run it at
session start, and it installs the pinned toolchain when what is present does not
satisfy the pins.

The general pattern — when it fits, when a provisioned image or an existing version
manager is the better answer, the install rules, and the traps that make a bootstrap
fail silently — is `tbd guidelines agent-session-bootstrap`. This document records only
what is specific to this repository.
For the setup path a human follows, see
[environment-bootstrap](runbooks/environment-bootstrap.runbook.md); for the policy the
bootstrap serves, see [supply-chain security](../SUPPLY-CHAIN-SECURITY.md).

## What It Installs

| Tool | Pin | Canonical source |
| --- | --- | --- |
| Node | 24.18.0 | `.node-version`, paired with `.nvmrc` |
| uv | 0.11.26 | the `required-version` floor in `uv.toml` |
| `gh` | 2.92.0 | the generated `ensure-gh-cli.sh`, which tbd owns |

The script reads each version from the file that already owns it, so it adds no second
copy to keep in step.
It carries the matching per-platform SHA-256 checksums, because those have nowhere else
to live: a mismatch deletes the file and exits nonzero, while an unreachable network
warns for that one tool and lets the session open.

Node is pinned rather than tracked, and the reason is specific here: a newer Node major
ships npm 12, outside the `>=11.10.0 <12` range in `package.json`, which
`engine-strict=true` turns into a hard `npm ci` failure.
The pinned Node carries npm 11.16.0, inside the range.

## Where It Is Wired

One agent-neutral script at `devtools/ensure-toolchain.sh`, invoked by path from both
`.claude/settings.json` and `.codex/hooks.json` rather than copied per agent.

It runs **first** in each agent’s `SessionStart` list, ahead of tbd’s own hook, which
needs the `npx` the bootstrap installs.
`tbd setup --auto` merges session hooks rather than replacing them, appending its
entries after those already configured, so a regeneration preserves that order.

## How It Is Guarded

`devtools/check_supply_chain.py` fails `make verify` when:

- `NODE_VERSION` or `UV_VERSION` in the script disagrees with `.node-version` or the
  `uv.toml` floor, so a pin bump that misses the script’s checksums cannot half-land;
- either agent’s configuration stops running the bootstrap, or stops running it first;
- either agent’s copy of a tbd-generated hook script stops matching its twin.
  Those three pairs (`ensure-gh-cli.sh`, `tbd-session.sh`, `tbd-closing-reminder.sh`)
  are written per agent rather than shared by path, so the check is what keeps a hand
  edit to one copy from drifting.
  Regenerate both with `tbd setup --auto`.

To bump a pin: change the canonical file, then update the version and the four
per-platform checksums in the script together.
Checksums come from `https://nodejs.org/dist/v<VERSION>/SHASUMS256.txt` and the uv
release’s `.sha256` asset.

## Extending It

The script is a list of `ensure_<tool>` functions sharing one checksum-verified download
path, so adding a tool means adding a function and its pinned checksums.
Each tool’s failure is its own: an unreachable download warns and returns rather than
skipping the tools after it.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
