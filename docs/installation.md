# Installation

Metaproc requires Python 3.12 or newer and is distributed through PyPI.

## Install uv

Install uv with the
[official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
On macOS, Homebrew is also supported:

```shell
brew install uv
```

Install a supported Python when needed:

```shell
uv python install 3.12
```

## Run Metaproc

After the first public release, run an exact version without a persistent installation:

```shell
uvx metaproc@0.1.0 --help
```

For a persistent global tool installation:

```shell
uv tool install metaproc
metaproc --help
```

Upgrade deliberately by naming the reviewed release:

```shell
uv tool install --upgrade metaproc==0.1.0
```

Cloud commands require an optional extra:

```shell
uv tool install "metaproc[gcp-batch]"
metaproc gcp --help
```

The base installation remains independent of Google Cloud packages and credentials.

## Run a Source Checkout

Before a version is published, or when developing locally, run the checked-out source
against its exact locks:

```shell
make install
uv --config-file uv.toml run --frozen metaproc --help
```

Run the offline quickstart:

```shell
uv --config-file uv.toml run --frozen metaproc run-process \
  examples/offline-smoke/offline-smoke.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=quickstart
```

## Install the Agent Skill

Metaproc composes its portable Agent Skill from the same package installed for the CLI:

```shell
metaproc skill metaproc --install
```

This writes a project-local `.agents/skills/metaproc/SKILL.md` and mirrors the same
payload to `.claude/skills/metaproc/SKILL.md`. Installed copies are generated artifacts;
update the packaged baseline and regenerate them instead of editing either copy by hand.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
