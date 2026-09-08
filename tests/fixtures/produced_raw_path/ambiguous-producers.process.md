---
process:
  name: ambiguous-producers
  steps:
    - id: stage-first
      mode: code
      command: "true"
      outputs:
        contested:
          path: "{{run.dir}}/contested.md"
          kind: file

    - id: stage-second
      mode: code
      command: "true"
      outputs:
        contested:
          path: "{{run.dir}}/contested.md"
          kind: file

    - id: reader
      mode: code
      needs: [stage-first, stage-second]
      command: "true"
      prompt_paths:
        - "{{run.dir}}/contested.md"
      outputs:
        reading:
          path: "{{run.dir}}/reading.md"
          kind: file
---

# Ambiguous Producers

Two upstream steps declare the same output path and neither is ordered against the
other, so which bytes `reader` sees is a race. A fingerprint cannot describe a race, so
the planner refuses the spec instead of picking a winner.
