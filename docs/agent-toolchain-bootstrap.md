# Agent Toolchain Bootstrap

Metaproc pins an exact Node and uv, and agent sessions start from a bare container that
has neither. Until the toolchain is installed, every Make target, `uv` command, and
`npm ci` fails, so each session begins with the same manual setup.
`devtools/ensure-toolchain.sh` removes that step: Claude Code and Codex both run it at
session start, and it installs the pinned toolchain when what is present does not
satisfy the pins.

This document records the pattern rather than the script, because it generalizes to any
repository with a pinned toolchain and agent contributors.
For the Metaproc-specific setup path, see
[environment-bootstrap](runbooks/environment-bootstrap.runbook.md); for the policy the
pattern serves, see [supply-chain security](../SUPPLY-CHAIN-SECURITY.md).

## When the Pattern Fits

Reach for a session bootstrap when all of these hold:

- **The toolchain is pinned and enforced.** Reproducibility already depends on exact
  versions, so installing “whatever is newest” is a defect rather than a convenience.
- **You do not control the base image.** Hosted agent sandboxes and cloud development
  environments hand you a container you did not build.
- **The failure mode is total.** A missing interpreter or package manager blocks every
  command, so the cost of a cold-start download is smaller than the cost of a blocked
  session.

## When Something Else Is Better

| Situation | Prefer |
| --- | --- |
| You build the image | Bake the toolchain into the Dockerfile or devcontainer; a session hook then finds it and exits immediately |
| The platform runs a setup step | Use the native lifecycle hook (`postCreateCommand` and equivalents) rather than duplicating it per agent |
| The team already uses a version manager | Have the hook invoke `mise`, `asdf`, or Nix instead of downloading, so one source of truth stays authoritative |
| The repository does not pin versions | Pin first. A bootstrap that installs an unpinned toolchain makes drift automatic instead of visible |

A session bootstrap is a repair mechanism for environments you cannot provision.
It is not a substitute for an image that ships the right tools.

## The Portable Parts

Seven properties carry across repositories and tools; the specific tools do not.

1. **One script, registered per agent.** Keep the logic in a single agent-neutral path
   and let each agent’s configuration only point at it.
   Per-agent copies drift, and the drift is silent until one agent’s sessions break.
2. **Read pins from their canonical files.** Resolve versions from the files that
   already own them, so the bootstrap adds no new copy to keep in step.
3. **Verify every download against a pinned checksum.** A bootstrap runs unattended and
   installs executables, so it is a supply-chain surface.
   Refuse a mismatch and delete the file.
4. **Separate tamper from unreachable.** A checksum mismatch is an attack signature and
   should stop hard; an unreachable network is an offline sandbox and should warn, then
   let the session open.
   Scope that warning to the one tool: an unreachable download should leave the tools
   after it still installed, or a single blocked host costs the session its whole
   toolchain.
5. **Install user-local, and make the tools resolvable.** A hook runs in its own shell,
   so later commands see the result only through a directory that is already on `PATH`.
   Point the package manager’s global prefix there too, or globally installed tools
   install successfully and then fail to resolve.
6. **Guard the pins in CI.** Assert that the bootstrap’s versions match their canonical
   files and that every supported agent still runs it, first.
   Without this, a version bump half-lands and a session installs the wrong toolchain,
   or a regenerated agent configuration seats another hook ahead of the bootstrap and
   that hook runs before the tools it needs exist.
7. **Stay idempotent and quiet.** Most sessions start with a satisfying toolchain; those
   should report one line and exit.

## Adapting It

The script is a list of `ensure_<tool>` functions sharing one checksum-verified download
path, so extending it to another tool means adding a function and its pinned checksums.
The same shape works for Go, Rust, Terraform, or a CLI like `gh`, which this repository
bootstraps the same way.

The costs are worth stating plainly.
A cold container pays a download on first start.
Each pin bump has to update the matching checksums, which is why the CI guard exists.
And the pattern assumes a user-local bin directory is on `PATH`, which is conventional
but not universal.

This is a candidate to graduate into a shared guideline rather than living in one
repository; `tbd guidelines supply-chain-hardening` is its natural neighbor.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
