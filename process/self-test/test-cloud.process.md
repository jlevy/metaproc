---
process:
  name: self-test-cloud-plan
  description: >-
    Standalone, no-dispatch GCP self-test. Renders a single-task Batch job
    for an installed-wheel smoke command. This validates cloud configuration
    and job construction without submitting a job or depending on a consumer.

  inputs:
    gcp_project: { param: GCP_PROJECT, as: string, required: true }
    image: { param: IMAGE, as: string, required: true }

  steps:
    - id: render-job
      mode: code
      command: >-
        bash -lc "cd ../.. && METAPROC_GCP_PROJECT={{gcp_project}} uv --config-file uv.toml run --frozen metaproc gcp run --dry-run --no-wheel --no-workspace --no-filestore --image {{image}} -- python -m metaproc --help"
      description: Render and validate the Batch job without submitting it.
---
# Cloud Plan Self-Test

Validates the standalone `metaproc gcp run` configuration and Batch job builder without
submitting a job. It deliberately uses `--dry-run`, ships no local workspace, mounts no
Filestore, and assumes the selected image already contains Metaproc.

## Usage

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/test-cloud.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=self-test-cloud-plan \
  --var GCP_PROJECT=your-project \
  --var IMAGE=us-central1-docker.pkg.dev/your-project/tools/metaproc:latest
```

For a live job, first inspect the rendered JSON and then run the equivalent direct
command without `--dry-run`. Cloud projects, images, service accounts, secrets,
networks, and downstream workflow packages remain operator-owned configuration.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
