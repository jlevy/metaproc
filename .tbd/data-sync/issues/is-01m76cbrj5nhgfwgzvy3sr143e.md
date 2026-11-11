---
type: is
id: is-01m76cbrj5nhgfwgzvy3sr143e
title: Drop the expired certifi pin and take certifi 2026.10.23
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m76a31xm6ap52ytabzbmhdt1
created_at: 2026-11-11T22:41:23.012Z
updated_at: 2026-11-11T22:41:23.012Z
---
Deferred from PR #122 (https://github.com/jlevy/metaproc/pull/122), which stays a one-package pyasn1 security bump. Covers two findings:

- A2 (Medium, review A, https://github.com/jlevy/metaproc/pull/122#pullrequestreview-5865991209): the certifi exclude-newer-package entry at uv.toml:7-26 (lock record uv.lock:11) is past its stated removal date. Both the uv.toml comment and SUPPLY-CHAIN-SECURITY.md:218-219 say to drop it "at the first ordinary relock after 2026-11-03". A per-package exclude-newer-package value replaces the global 14-day window for that package, so the entry now acts as a ceiling: make upgrade cannot see any certifi release after 2026-10-20.
- B1 (Medium, review B, https://github.com/jlevy/metaproc/pull/122#pullrequestreview-5866218020): what the cap holds back. certifi 2026.10.23 (uploaded 2026-10-23T11:36:14Z, cleared the cool-off 2026-11-06, not yanked, wheel carries a trusted-publishing attestation from certifi/python-certifi release.yml) changes only certifi/__init__.py and certifi/cacert.pem relative to 2026.10.19. The bundle goes from 120 to 118 certificates: it removes "emSign Root CA - C1" and "emSign ECC Root CA - C3" and adds none. Those are two of the "three unaffected emSign roots" SUPPLY-CHAIN-SECURITY.md:206-208 says 2026.10.19 kept, so the recorded trust decision is out of date. Every environment synced from the lock still trusts those two roots; certifi backs requests/httpx/httpcore in the gcp and browser extras and dev groups, including GCP token requests. make audit stays green because no advisory is listed against 2026.10.19.

Dry-run evidence (reported by reviews A and B, reproduced by B, at PR #122 head 185ac28):
- With uv.toml as committed, `uv lock --upgrade-package certifi --dry-run` reports "No lockfile changes detected".
- With the certifi entry removed, it reports "Update certifi v2026.10.19 -> v2026.10.23".
- Review A: removing the entry and running make lock (no upgrade) removes exactly uv.lock:11 and changes no package.

Open question: why upstream removed the two emSign roots. 2026.10.23 is an automated bundle sync (upstream commit b60466c) following the upstream root store; the root program's decision record was not read. Review B: if the removal is a distrust decision tied to the incident behind GHSA-6q48-6c7w-9gw2, treat this as High.

Work: delete the certifi entry and comment in uv.toml, run `uv --config-file uv.toml lock --upgrade-package certifi` under the project config, audit 2026.10.23 as a trust-store change, and replace SUPPLY-CHAIN-SECURITY.md:206-208 and :218-219 with the move to 2026.10.23 and the two removed roots. Both reviewers leaned toward doing this promptly; it is a trust-store reduction with nothing added.
