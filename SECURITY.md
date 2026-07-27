# Security Policy

## Supported Versions

Security fixes are provided for the latest published Metaproc release.
During the `0.x` series, upgrades may include compatibility changes documented in the
release notes.

## Reporting a Vulnerability

Do not open a public issue for a vulnerability.
Use GitHub’s private vulnerability reporting for
[jlevy/metaproc](https://github.com/jlevy/metaproc/security) when it is available, or
contact the maintainer through the email address in the package metadata.

Include the affected version, impact, reproduction steps, and a minimal sanitized
fixture. Do not send credentials, private logs, customer data, cloud snapshots, or an
archive of a workflow run.

## Security Boundaries

Metaproc runs external agent CLIs and user-authored Python or shell handlers with the
operator’s permissions.
Process specs and plugins are executable code; run them only from trusted sources.

Metaproc does not provide a tenant-isolation boundary.
Local run directories can contain prompts, model output, tool traces,
environment-derived settings, and credentials emitted by external tools.
Store them under access controls appropriate to that data and sanitize them before
sharing.

Cloud dispatch can create compute, storage, logging, and secret-manager activity in the
configured GCP project.
Use dedicated service accounts with least privilege.
Metaproc passes secret references, not secret payloads, through dispatch manifests.

The bundled Metabrowser plugin executes JavaScript in a Metabrowser page and may expose
run artifacts selected by the operator.
Load the plugin and serve data only within trusted local or network boundaries.

Reports of command injection, path traversal, credential disclosure, cross-account
credential reuse, manifest tampering, unsafe archive handling, or plugin trust bypass
are security issues.

See [supply-chain security](SUPPLY-CHAIN-SECURITY.md) for dependency and build policy.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
