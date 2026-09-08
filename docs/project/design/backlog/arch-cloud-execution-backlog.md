# Architecture: Cloud Execution: Future Work

Backlog extracted from
[arch-cloud-execution.md](../../../../src/metaproc/docs/arch-cloud-execution.md), which
ships in the wheel and describes the system as it is.
Where it might go is a project record and lives here.

## Future Considerations

### Open Questions

- Which durable transport should implement local-orchestrator/cloud-worker state,
  events, leases, cancellation, and recovery without making a laptop-mounted filesystem
  authoritative?
- Should the first per-step worker-profile extension live in process-spec data or in a
  separately referenced placement profile?
- Should `SecretRefSet` provider-ref aggregation be lazy (current) or eagerly validated
  at dispatch time? The current design silently skips unresolvable provider refs, which
  could mask a misconfigured credential until the adapter fails at runtime.
- `billing.py` approximates billable hours from machine type and runtime spans but
  cannot reconcile against actual GCP invoices.
  Is the approximation accurate enough for attribution, or should it be replaced with
  Billing API queries?

### Potential Improvements

- `run_cloud_preflight()` validates env-var presence but does not probe GCP API
  reachability (e.g., can the Batch API be called?
  Is the Filestore server resolvable?). Adding a lightweight API probe could catch
  misconfigured networks before job submission.
