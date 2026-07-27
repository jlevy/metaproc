---
process:
  name: synthetic-auth-env-pi
  defaults:
    default_adapter: pi-cli
    adapters:
      pi-cli:
        type: pi-cli
  steps:
    - id: synthetic-pi-step
      mode: agent
      prompt_prefix: "Return the word OK."
      output_root: "{{run.dir}}/synthetic-pi-step"
---
# Synthetic PI authentication fixture

Minimal deterministic process used to test adapter-set authentication checks.
