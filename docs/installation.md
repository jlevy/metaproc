# Installation

Metaproc requires Python 3.14 or newer and is distributed through PyPI.

## Install uv

Install uv with the
[official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
On macOS, Homebrew is also supported:

```shell
brew install uv
```

Install a supported Python when needed:

```shell
uv python install 3.14
```

## Run Metaproc

Run an exact version without a persistent installation:

```shell
uvx metaproc@0.2.1 --help
```

For a persistent global tool installation:

```shell
uv tool install metaproc
metaproc --help
```

Upgrade deliberately by naming the reviewed release:

```shell
uv tool install --upgrade metaproc==0.2.1
```

Cloud commands require an optional extra:

```shell
uv tool install "metaproc[gcp-batch]"
metaproc gcp --help
```

The base installation remains independent of Google Cloud packages and credentials.

### Optional YAML C extension

Metaproc does not install `ruamel.yaml.clib`. When the extension is present, the
supported frontmatter-format and Softschema releases use their checked C parser on
compatible inputs and fall back to the pure parser for guarded syntax or parser errors.
Their public `yaml_parser_mode()` functions report `c-first` or `pure` for the current
environment. `FRONTMATTER_FORMAT_PURE_YAML=1` and `SOFTSCHEMA_PURE_YAML=1` force the
corresponding reader to use the pure parser.

Frontmatter-format’s YAML factories and Metaproc’s pool writers keep deterministic
pure-Python emitters with or without the extension.
See the
[YAML I/O gotchas](../src/metaproc/docs/arch-file-io-utilities.md#frontmatter_format-gotchas)
for the writer/reader boundary.

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
