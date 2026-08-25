---
type: is
id: is-01m0txm9k40pwdbx279nqezdy5
title: Remove the expired cryptography advisory waiver and relock before 0.3.0
kind: bug
status: closed
priority: 0
version: 2
labels:
  - security,supply-chain,release
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:18:04.260Z
updated_at: 2026-08-25T02:37:49.380Z
closed_at: 2026-08-25T02:37:49.380Z
close_reason: "Fixed in the 0.3.0 release prep. cryptography relocked 49.0.0 -> 50.0.0, --ignore GHSA-g6cj-pr64-35w5 dropped from AUDIT_IGNORES in the Makefile, and the waiver entry replaced with a no-waivers statement in SUPPLY-CHAIN-SECURITY.md. npm audit is clean locally; the uv/osv.dev half is confirmed by CI, which now runs the audit unfiltered (api.osv.dev returns x-deny-reason: host_not_allowed from this sandbox, a real network policy denial, so it was not worked around)."
resolution: null
duplicate_of: null
---
## Finding

`make audit` still suppresses a high-severity advisory whose documented removal condition was met 10 days ago.

`Makefile:68`:
```
AUDIT_IGNORES ?= --ignore GHSA-g6cj-pr64-35w5
```

SUPPLY-CHAIN-SECURITY.md lines 76-82 describe the waiver and set its own expiry:

> `GHSA-g6cj-pr64-35w5` / `CVE-2026-69247` (high, CVSS 8.2), a Bleichenbacher oracle in the `cryptography` `pkcs7` `EnvelopedData` decryption path. [...] The fix, `cryptography` 50.0.0, was published 2026-07-31 and is inside the 14-day cool-off until roughly 2026-08-14. **Remove the waiver and relock once it is eligible.**

Today is 2026-08-24. `uv.lock:337-338` still pins `cryptography 49.0.0`, the vulnerable version. The fix has been outside the cool-off and eligible for roughly 10 days, and the rolling 14-day window currently admits anything published before ~2026-08-10, so 50.0.0 (2026-07-31) qualifies.

## Why this blocks the release

Cutting 0.3.0 with the waiver in place ships a release whose audit gate is green only because it is told to ignore a CVSS 8.2 finding, against the repository's own written policy. The reachability argument in the waiver (indirect via `google-auth` under the `gcp` extra, `pkcs7` never imported) is sound and is why this is not an emergency, but it was explicitly a time-boxed exception, and the box has expired.

## Action

1. `make lock` (or `uv lock --upgrade-package cryptography`) to take `cryptography` 50.0.0.
2. Drop `--ignore GHSA-g6cj-pr64-35w5` from `AUDIT_IGNORES` in the Makefile.
3. Delete the waiver paragraph from SUPPLY-CHAIN-SECURITY.md, leaving the "Audited Advisory Waivers" section with no active waiver.
4. Run `make audit` unfiltered and confirm it is clean on its own merits.
5. Commit `uv.lock`.

## Caveat on verification

The audit could not be run in this container: `uv audit` fails with `error sending request for url (https://api.osv.dev/v1/querybatch) / tunnel error: unsuccessful`, and `npm audit` needs the registry too. The advisory status above comes from the repository's own documentation and lockfile, not from a live audit. Re-run `make audit` somewhere with network access to confirm nothing else has appeared since the lock was last refreshed.
