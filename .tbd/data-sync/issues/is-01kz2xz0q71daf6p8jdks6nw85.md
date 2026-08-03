---
type: is
id: is-01kz2xz0q71daf6p8jdks6nw85
title: Prevent unresolved-owner usage double counting
kind: bug
status: in_progress
priority: 1
version: 2
labels:
  - pr-6
  - review
dependencies: []
parent_id: is-01kz2xyqqrkherk08h96kw58k9
created_at: 2026-08-03T04:26:27.430Z
updated_at: 2026-08-03T04:26:37.170Z
---
Resource rollups must not add file-level unattributed metrics on top of already-attributed event metrics when ownership cannot be resolved.
