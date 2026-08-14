---
type: is
id: is-01kyx385g5c392kmb9zga9qhm6
title: Rename structure-report contract id to the 0.3 grammar
kind: task
status: closed
priority: 1
version: 3
labels:
  - softschema
dependencies:
  - type: blocks
    target: is-01kyx38gn4gwmp93rst4psbm0x
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:23.524Z
updated_at: 2026-07-31T22:27:49.695Z
closed_at: 2026-07-31T22:27:49.694Z
close_reason: "Renamed to metaproc:StructureReport/v1; landed in #3 with CHANGELOG migration note"
---
softschema 0.3 validates Contract.id against [namespace:]Name[/version]. Eight of nine built-in ids already comply; 'metaproc.structure_report.v1' does not. Rename to 'metaproc:StructureReport/v1' across registry.py, structure_report.py, commands/softschema.py and tests. Artifacts written by earlier versions no longer validate - migration noted in CHANGELOG.
