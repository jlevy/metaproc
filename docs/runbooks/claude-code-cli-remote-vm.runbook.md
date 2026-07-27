---
runbook:
  title: "Claude Code CLI on a Remote GCP VM (Debugging Appendix)"
  description: Debugging appendix — the per-dev VM + SSHFS path for Personal-Plan Claude Code CLI. The primary path is GCP Batch via `METAPROC_GCP_SECRET_CLAUDE_CREDS`; see cloud-dispatch.runbook.md §4a.
  category: metaproc
---
# Claude Code CLI on a Remote GCP VM (Debugging Appendix)

> **Superseded as the primary flow.** The canonical path for Personal-Plan
> `variant=claude-code-cli` dispatch is now GCP Batch with the Keychain blob shipped
> through Secret Manager via `metaproc claude-auth push` — see
> [`cloud-dispatch.runbook.md` §4a](cloud-dispatch.runbook.md) and
> [`credential-setup.runbook.md`](credential-setup.runbook.md).
> The Batch flow writes directly to kernel-NFS Filestore on the worker, so the FUSE
> wedge failure mode this runbook was designed to avoid no longer applies.
> This document is retained as a **debugging appendix** for the per-dev-VM approach and
> for interactive `claude` sessions off the laptop; it is not the recommended production
> path.

Operator runbook for running `variant=claude-code-cli` on a GCP VM instead of the macOS
laptop.

## When to use this

The Claude Code CLI adapter
([`src/metaproc/adapters/claude_code.py`](../../src/metaproc/adapters/claude_code.py))
authenticates via interactive-login session state — not `ANTHROPIC_API_KEY`. On macOS
that state lives in the login Keychain, which pins execution to the dev laptop.
When the laptop dispatches work directly to Filestore via SSHFS/FUSE-T, a single dropped
SSH session can wedge the entire mount and cascade every worker into uninterruptible
wait (`U` state), recoverable only by reboot.
See [`docs/arch/arch-metaproc-core.md`](../arch/arch-cloud-execution.md) for the full
failure analysis.

This runbook documents the verified alternative: ship the Keychain-held OAuth blob to a
GCP VM and run `claude -p` there.
The VM mounts Filestore natively via kernel NFS, so the laptop is no longer in the I/O
path and FUSE wedges cannot cascade into a run.

**Use this for:** any laptop-pinned `variant=claude-code-cli` workload that needs to
write durably to Filestore (Phase 2c Opus gold-set dispatches, calibration passes, large
judge runs).

**Do not use this for:** API-key-authenticated variants (`pi-opus`, `pi-sonnet-*`, etc.)
— they already run fine on GCP Batch with `--backend gcp-worker`.

## Auth portability — what is and isn’t portable

Verified empirically against Claude Code 2.1.108 (macOS host) and 2.1.114 (Debian 12 VM)
on 2026-04-19.

| Piece | Location on macOS | Required on Linux VM? | Notes |
| --- | --- | --- | --- |
| OAuth tokens (access + refresh) | macOS Keychain entry `Claude Code-credentials` (~530 bytes JSON) | **Yes** — must be written to `~/.claude/.credentials.json`, mode `0600` | This is the only file that matters for auth. |
| Account metadata (`~/.claude.json`, `~/.claude/settings.json`, projects, sessions, etc.) | `~/.claude.json` + `~/.claude/` | **No** | Optional — the CLI recreates what it needs on first run. |
| ANTHROPIC_API_KEY | env var | **No** — and **do not set it on the VM** | If set, the adapter prefers it over the session credential, which defeats the purpose of using the subscription. |

The Keychain blob schema is:

```json
{
  "claudeAiOauth": {
    "accessToken": "sk-ant-...",
    "expiresAt": <unix-ms>,
    "rateLimitTier": "...",
    "refreshToken": "...",
    "scopes": [...],
    "subscriptionType": "..."
  },
  "organizationUuid": "..."
}
```

On Linux, Claude Code reads exactly this JSON from `~/.claude/.credentials.json`. The
access token has a short life (~hours); the CLI uses the refresh token to rotate
automatically as long as the VM has outbound HTTPS. **No re-login required on the VM as
long as the refresh token stays valid** — typically many weeks.

## Prerequisites

- You are signed into Claude Code on your Mac with an active subscription
  (`security find-generic-password -s "Claude Code-credentials" -w` returns the JSON
  above).
- GCP project access (`roles/compute.osLogin` or equivalent).
- A target VM with `/mnt/filestore/` mounted via kernel NFS. `metaproc-browser`
  (e2-micro, us-central1-b) already has this — fine for smoke tests but **too small for
  concurrent real runs**. For production use provision a larger instance (e2-standard-2
  or e2-standard-4 for `max_concurrency: 10`).

## One-time VM setup

Run these once per VM. Skip whichever step is already done (`which claude` on the VM
tells you).

```bash
# 1. Install Node 22 + npm.
gcloud compute ssh <vm-name> --zone <zone> --project exampletool --command "
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - &&
  sudo apt-get install -y nodejs
"

# 2. Install Claude Code globally.
gcloud compute ssh <vm-name> --zone <zone> --project exampletool --command "
  sudo npm install -g @anthropic-ai/claude-code@2.1.126 &&
  claude --version
"
```

Expected output on step 2: `X.Y.Z (Claude Code)`.

## Sync credentials from laptop Keychain to VM

Run this every time the refresh token expires (rare) or when setting up a new VM. The
JSON is piped through SSH’s stdin — it never lands in a local file.

```bash
security find-generic-password -s "Claude Code-credentials" -w |
  gcloud compute ssh <vm-name> --zone <zone> --project exampletool --command "
    mkdir -p \$HOME/.claude &&
    cat > \$HOME/.claude/.credentials.json &&
    chmod 600 \$HOME/.claude/.credentials.json &&
    echo OK
  "
```

### Security notes

- The credential grants full Claude Code subscription access as you.
  Treat the VM like any host that can charge to your account.
- Only sync to a VM you personally own — not a shared runner, not a CI worker, not the
  `metaproc-browser` gateway (which is reachable by anyone with project-level GCP IAM).
- If the VM is torn down or repurposed, wipe the file first:
  `shred -u ~/.claude/.credentials.json` (fallback: `rm -f`).
- Rotating on the laptop (logout/login in Claude Code) invalidates the copy on the VM.
  Re-sync after every laptop re-auth.

## Smoke test

Two commands. First verifies auth end-to-end; second verifies the Filestore write path.

```bash
# Smoke 1: auth works on the VM.
gcloud compute ssh <vm-name> --zone <zone> --project exampletool --command "
  claude -p 'Reply with exactly the single word: PORTABLE' --output-format json
" | jq -r '.result'
# Expected: PORTABLE

# Smoke 2: the output reaches Filestore through the kernel NFS client.
gcloud compute ssh <vm-name> --zone <zone> --project exampletool --command "
  sudo mkdir -p /mnt/filestore/runs/_smoke-\$(date +%Y%m%d-%H%M%S) &&
  sudo chown \$USER:\$USER /mnt/filestore/runs/_smoke-* &&
  cd /mnt/filestore/runs/_smoke-* &&
  claude -p 'Write exactly: FILESTORE_WRITE_OK' > result.txt &&
  cat result.txt &&
  cd / && sudo rm -rf /mnt/filestore/runs/_smoke-*
"
# Expected: FILESTORE_WRITE_OK
```

If smoke 1 prints a login URL instead of `PORTABLE`, the credential sync did not land —
`~/.claude/.credentials.json` is missing, wrong mode, or was replaced by a concurrent
`claude` session on the VM recreating the file from scratch.

## Run a real metaproc dispatch on the VM

Once the smoke test passes, run `metaproc run-process` on the VM the same way you would
on the laptop. The VM’s `RUNS_DIR` should point at `/mnt/filestore/runs/` directly — no
`rsync` stage needed, because kernel NFS handles drops without cascading.

```bash
# On the VM (via gcloud compute ssh --command, or interactively).
export RUNS_DIR=/mnt/filestore/runs
cd /path/to/checked-out/consumer
uv run metaproc run-process <process-dir> \
  --backend local \
  --variant claude-code-cli \
  --max-concurrency 10 \
  --var RUN_ID=<run-id> \
  --var RUNS_DIR="$RUNS_DIR" \
  <other flags>
```

The VM must have enough RAM for `max_concurrency` Claude Code workers.
Rule of thumb: ~400 MB per worker at steady state plus overhead — e2-standard-4 (16 GiB)
comfortably handles concurrency 10.

## Caveats

- **Refresh-token lifetime is not publicly documented.** If a run fails with an auth
  error after several weeks of reuse, re-sync from the laptop.
- **MFA / periodic re-login prompts** are browser-based on the laptop.
  Those complete on the Mac; the refreshed token is then available in the Keychain and
  can be re-synced to the VM. There is no way to complete the MFA flow on a headless VM.
- **Pool-shared VMs are not safe.** Each dev should have their own VM (or bring up
  ephemeral ones per dispatch).
  The credential file is per-human.
- **Filestore permissions.** `/mnt/filestore/runs/` is root-owned on the gateway VM
  today. The Batch-worker VMs mount with permissive ownership because they run as root;
  dev-owned VMs need `sudo chown`-ing specific subdirs before the process can write.
  Consider adding a `metaproc-runners` group and chgrp’ing `runs/` to it when this
  pattern matures.

## Related

- Research brief (failure analysis + alternatives):
  [`docs/arch/arch-metaproc-core.md`](../arch/arch-cloud-execution.md)
- Original tactical fix (local APFS + rsync-after): commit `b787061dc`
- Adapter source:
  [`src/metaproc/adapters/claude_code.py`](../../src/metaproc/adapters/claude_code.py)
- Cloud mount and browsing guidance:
  [cloud execution architecture](../arch/arch-cloud-execution.md)

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
