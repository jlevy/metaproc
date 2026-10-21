---
type: is
id: is-01m5dmeza78g84sp6n95jjx6x6
title: "PR #107 F1: confirm the GCP cloud image /opt/venv runs Python 3.14 before releasing the 3.14 floor"
kind: bug
status: open
priority: 1
version: 2
labels: []
dependencies:
  - type: blocks
    target: is-01m5dmf0426xcenc926ajgrrxr
parent_id: is-01m5dmedq0w40wpkbx1qt44zr7
created_at: 2026-10-20T21:46:14.213Z
updated_at: 2026-10-20T21:46:28.929Z
---
PR #107 raises \`requires-python\` to \`>=3.14,<4.0\` so \`compression.zstd\` is available from the standard library.

The GCP batch entrypoint force-reinstalls the shipped Metaproc wheel into \`/opt/venv\` with \`uv pip install\` when \`METAPROC_WHEEL_GCS\` is set (\`src/metaproc/docs/arch-cloud-execution.md:761-763\`). That venv Python is fixed by an image build that lives outside this repository, so it could not be verified from the checkout. If it is below 3.14, \`uv pip install\` refuses the wheel and GCP dispatch breaks at container bootstrap, not at build or test time.

Action: confirm the image interpreter, and bump the image build if needed, before a release carries the 3.14 floor. Recorded in \`docs/project/design/artifact-compression.md:232\`.

Related consequence of the same floor raise: 3.12 and 3.13 environments cannot install the next release, and the CI test matrix is now a single version (\`.github/workflows/ci.yml:67\`).
