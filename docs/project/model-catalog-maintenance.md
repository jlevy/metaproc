# Model Catalog Maintenance

Metaproc validates explicit model selections against the reviewed catalog in
[`model_catalog.py`](../../src/metaproc/config/model_catalog.py).
Validation prevents a misspelled or unknown model from being silently replaced by an
adapter default. It does not promise that a model is available to every account or works
through every provider route.

The catalog owner is the Metaproc maintainer.
Each refresh records the named person or agent who performed the review and a separate
named reviewer in the pull request or tbd issue.
The catalog’s `reviewed_on` date records the completed evidence review.

## Evidence Boundaries

Treat these checks as separate claims:

1. **API catalog existence:** The provider publishes the exact model ID and lifecycle
   status in an official catalog or deprecation page.
2. **CLI parsing:** The pinned CLI version accepts the ID and preserves it in the
   constructed command.
   A CLI may also resolve aliases or route failures to another model.
3. **Provider availability:** The authenticated account, plan, project, region, and API
   surface can serve the model.
   Catalog inclusion cannot establish this.
4. **Live compatibility:** A bounded probe observes the requested model and completes a
   representative tool call.
   Record the route and date; do not generalize the result to untested routes.

Prefer canonical versioned IDs for repeatable work.
Moving aliases such as `pro`, `flash`, and `latest` are useful operator choices, but
their target can change without a Metaproc release.
Keep retired IDs only when existing configuration or historical-run validation requires
them, and label them as historical rather than current.

## Refresh Triggers

Review the catalog every 30 days, as set by `MODEL_CATALOG.review_interval_days`, and
also when:

- a provider announces a model release, replacement, or deprecation
- a pinned agent CLI is upgraded
- an adapter reports an unknown model, unexpected fallback, or observed-model mismatch
- a process profile proposes a new default or explicit model

Google does not publish a universal 45-day retirement rule.
Use its model-specific deprecation schedule.
The catalog’s `lifecycle_notes` records dated retirements, retained historical IDs, and
unverified routes. Track replacement decisions before their retirement deadlines.

## Refresh Procedure

1. Record the review owner, reviewer, date, pinned CLI versions or commits, and scope.
2. Open every primary source in `MODEL_CATALOG.sources`. Compare exact IDs, aliases,
   lifecycle status, supported input and output, tool use, and reasoning controls.
3. Compare the provider evidence with `native_models`, `defaults`, and
   `lifecycle_notes`. Do not add image-only, embedding-only, or live-only models to a
   text-agent adapter unless that adapter supports the surface.
4. Inspect each pinned CLI’s model parsing, alias resolution, and effort syntax.
   Record evidence from the repository-pinned version; an installed older CLI or an app
   model catalog does not establish that contract.
5. Review `pi-models.default.json`. `known_model_ids("pi-cli")` derives custom-provider
   IDs from this packaged file and combines them with native Pi aliases, so there is no
   second manual Pi whitelist.
6. Decide explicitly whether each new ID is current, preview, or historical-only.
   Decide default migrations separately; a catalog refresh must not silently move an
   existing default.
7. Review pricing in [`pricing.md`](../../src/metaproc/data/pricing.md) as a separate
   evidence pass. Do not infer rates from model availability.
8. Add command-construction and rejection tests.
   Where credentials and budget permit, run a bounded model-identity and tool-call probe
   and label its exact route.
9. Run `make verify`. Confirm the catalog and packaged Pi data are present in the built
   wheel and that the isolated installed-wheel smoke checks pass.

New OpenAI models routed through Pi may require per-model Responses API overrides.
Add those overrides deliberately while preserving the provider’s existing defaults for
older models, then test both an overridden model and an unchanged model.

The update evidence must state what was added, retained, retired, or left uncertain; the
default and retirement migration decisions; the checks run; and any provider routes that
remain unverified. Allowlisting alone is never evidence of a live test.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
