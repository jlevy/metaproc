---
process:
  name: staged-then-read
  steps:
    - id: stage-source-snapshot
      mode: code
      command: "true"
      prompt_paths:
        # Its own output. A step that reads and rewrites one path must keep that path in
        # its own fingerprint, so the self-exclusion branch has to be consulted here.
        - "{{run.dir}}/company-profile.md"
      outputs:
        company_profile:
          path: "{{run.dir}}/company-profile.md"
          kind: file

    - id: stage-oddly-spelled
      mode: code
      command: "true"
      outputs:
        segment_note:
          path: "{{run.dir}}//segment-note.md"
          kind: file

    - id: decompose
      mode: code
      needs: [stage-source-snapshot, stage-oddly-spelled]
      command: "true"
      prompt_paths:
        - "{{run.dir}}/company-profile.md"
        - "{{run.dir}}/segment-note.md"
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

`stage-oddly-spelled` declares its output with a doubled slash while the reader spells it
plainly. The two are the same file, so the match has to be keyed the way every other
authored-path comparison in the module is keyed, not by raw string equality.
