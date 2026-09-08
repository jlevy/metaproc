---
process:
  name: unrelated-and-downstream
  steps:
    - id: stage-upstream
      mode: code
      command: "true"
      outputs:
        upstream_note:
          path: "{{run.dir}}/upstream-note.md"
          kind: file

    - id: unrelated-writer
      mode: code
      command: "true"
      outputs:
        unrelated_note:
          path: "{{run.dir}}/unrelated-note.md"
          kind: file

    - id: reader
      mode: code
      needs: [stage-upstream]
      command: "true"
      prompt_paths:
        # Upstream: ordered before this step, so the run does write it first.
        - "{{run.dir}}/upstream-note.md"
        # Unrelated: nothing orders it before this step.
        - "{{run.dir}}/unrelated-note.md"
        # Downstream: written by a step that waits on this one.
        - "{{run.dir}}/downstream-note.md"
      outputs:
        reading:
          path: "{{run.dir}}/reading.md"
          kind: file

    - id: downstream-writer
      mode: code
      needs: [reader]
      command: "true"
      outputs:
        downstream_note:
          path: "{{run.dir}}/downstream-note.md"
          kind: file
---

# Unrelated And Downstream

`reader` references three raw paths. Only `upstream-note.md` has a producer ordered
before it, so only that one is execution state its fingerprint must exclude.

`unrelated-note.md` is written by a step with no dependency path to `reader`, and
`downstream-note.md` is written by a step that waits on `reader`. Neither supplies
anything to this read. Treating either as produced would drop a real authored file out
of both the existence check and the content fingerprint.
