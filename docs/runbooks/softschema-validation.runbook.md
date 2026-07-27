---
runbook:
  title: Softschema Validation
  description: Validating softschema-tagged artifacts with the standalone softschema package; pointers to the reusable validation guidance.
  category: metaproc
---
# Softschema Validation Runbook

Reusable `softschema` validation guidance lives in the standalone package docs:

- [softschema-guide.md](https://github.com/jlevy/softschema/blob/main/docs/softschema-guide.md)
  — adoption guide and validation playbooks.
- [softschema-spec.md](https://github.com/jlevy/softschema/blob/main/docs/softschema-spec.md)
  — artifact format and validation semantics.
- [softschema-python-design.md](https://github.com/jlevy/softschema/blob/main/docs/softschema-python-design.md)
  — Python package design decisions, including the validation pipeline.

You can read these locally without leaving the terminal:

```bash
softschema docs guide
softschema docs spec
softschema docs python-design
softschema docs --list
```

Metaproc still owns the commands that load process specs and plugins:

```bash
uv run metaproc structure-report path/to/workflow.process.md
uv run metaproc softschema validate path/to/artifact.md --schema example.record.v1
uv run metaproc softschema compile module:Model --out schemas/model.schema.yaml --check
```

Use the package docs for reusable behavior and this path as a compatibility pointer for
older Metaproc references.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
