---
process:
  name: fingerprint-smoke
  description: |
    Three-step linear DAG used by the edit-and-rerun integration test for
    plan-2026-05-20-metaproc-step-fingerprint-and-status.md. Each code step
    references its own runbook via prompt_paths so the per-step fingerprint
    includes the runbook bytes. Editing one runbook flips that step's
    fingerprint; the orchestrator must cache step 1 and re-execute the
    edited step plus its downstream.

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
      handler: "fingerprint_smoke_handlers.py:step_one"
      prompt_paths: [deps.step1_runbook]
      outputs:
        out:
          path: "{{run.dir}}/s1.txt"
          kind: file

    - id: s2
      mode: code
      handler: "fingerprint_smoke_handlers.py:step_two"
      prompt_paths: [deps.step2_runbook]
      needs: [s1]
      outputs:
        out:
          path: "{{run.dir}}/s2.txt"
          kind: file

    - id: s3
      mode: code
      handler: "fingerprint_smoke_handlers.py:step_three"
      prompt_paths: [deps.step3_runbook]
      needs: [s2]
      outputs:
        out:
          path: "{{run.dir}}/s3.txt"
          kind: file
---
# Fingerprint Smoke Fixture

Used by `test_step_fingerprint_smoke.py` to exercise the Phase 1 edit-and-rerun cascade
end-to-end.
