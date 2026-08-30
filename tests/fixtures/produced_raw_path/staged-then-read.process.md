---
process:
  name: staged-then-read
  steps:
    - id: stage-source-snapshot
      mode: code
      command: "true"
      outputs:
        company_profile:
          path: "{{run.dir}}/company-profile.md"
          kind: file

    - id: decompose
      mode: code
      needs: [stage-source-snapshot]
      command: "true"
      prompt_paths:
        - "{{run.dir}}/company-profile.md"
        - "{{run.dir}}/authored-input.md"
      outputs:
        breakdown:
          path: "{{run.dir}}/breakdown.md"
          kind: file
---

# Staged Then Read

The shape released specs use: one step writes a snapshot into the run dir, a later step
reads it by raw path. The read path is execution state, not an authored input, so its
bytes must leave the reader's fingerprint. `authored-input.md` is written by nothing here
and must stay in it.
