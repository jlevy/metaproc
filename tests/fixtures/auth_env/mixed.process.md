---
process:
  name: synthetic-auth-env-mixed
  defaults:
    default_adapter: pi-cli
    adapters:
      pi-cli:
        type: pi-cli
      claude-code-cli:
        type: claude-code-cli
  steps:
    - id: synthetic-pi-step
      mode: agent
      prompt_prefix: "Return the word PI."
      output_root: "{{run.dir}}/synthetic-pi-step"
    - id: synthetic-claude-step
      mode: agent
      adapter:
        type: claude-code-cli
      prompt_prefix: "Return the word CLAUDE."
      output_root: "{{run.dir}}/synthetic-claude-step"
---
# Synthetic mixed authentication fixture

Minimal deterministic process used to test adapter-set authentication checks.
