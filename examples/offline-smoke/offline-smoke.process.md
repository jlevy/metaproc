---
process:
  name: offline-smoke
  description: |
    Three-step linear DAG that runs without an agent CLI, network access, cloud
    credentials, or optional GCP dependencies. Each code step references its own
    runbook so the example also exercises dependency resolution and fingerprints.

  deps:
    step1_runbook:
      path: ./step1-runbook.md
      as: path
    step2_runbook:
      path: ./step2-runbook.md
      as: path
    step3_runbook:
      path: ./step3-runbook.md
      as: path

  steps:
    - id: s1
      mode: code
      handler: "offline_smoke_handlers.py:step_one"
      prompt_paths: [deps.step1_runbook]
      outputs:
        out:
          path: "{{run.dir}}/s1.txt"
          kind: file

    - id: s2
      mode: code
      handler: "offline_smoke_handlers.py:step_two"
      prompt_paths: [deps.step2_runbook]
      needs: [s1]
      outputs:
        out:
          path: "{{run.dir}}/s2.txt"
          kind: file

    - id: s3
      mode: code
      handler: "offline_smoke_handlers.py:step_three"
      prompt_paths: [deps.step3_runbook]
      needs: [s2]
      outputs:
        out:
          path: "{{run.dir}}/s3.txt"
          kind: file
---
# Offline Smoke Process

This process is the source-checkout quickstart and deterministic execution smoke test.
It writes three step outputs and an invocation log beneath the selected run directory.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
