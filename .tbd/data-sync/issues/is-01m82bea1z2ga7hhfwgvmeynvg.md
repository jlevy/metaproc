---
type: is
id: is-01m82bea1z2ga7hhfwgvmeynvg
title: Verify current model catalog and retained model routes
kind: task
status: in_progress
priority: 1
version: 4
labels: []
dependencies:
  - type: blocks
    target: is-01m82bedqvj9ka2vb2xcza34ar
parent_id: is-01m82be4yjpzsaestvwb057vxx
created_at: 2026-11-22T19:24:01.982Z
updated_at: 2026-11-22T19:36:08.784Z
---
Check official model capabilities, exact routing, lifecycle and prices, refresh omissions, and exercise latest and retained profiles in a downstream comparison.

## Notes

Nov 22 provider review found a missing textual SKU on the direct Moonshot route. Added Kimi K3.1 Pro Highspeed and corrected five-minute cache-write rates. Pi 1.5.2 lists the exact custom provider and model. Focused model, accounting and resume tests passed (229). Live Moonshot inference requires its own credential. Gemini 3.6 stayed exact and the documented 3.7 Vertex shutdown is January 28, 2027. Six downstream exact model live comparisons from the #126 release base were rechecked by strict downstream dry-runs.
