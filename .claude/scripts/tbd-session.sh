#!/bin/bash
# Ensure the tbd CLI is available and run `tbd prime`.
# Installed by: tbd setup --auto. Runs on SessionStart and PreCompact.
#
# This hook never downloads or executes a missing package. Install the pinned
# CLI explicitly before using the repository's issue-tracking integration.

# Prefer common local bin locations.
export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"

# Local-first: use tbd if it is already on PATH.
if command -v tbd &> /dev/null; then
    tbd prime "$@"
    exit $?
fi

echo "[tbd] tbd CLI not found."
echo "[tbd] Install it with: npm install -g get-tbd@0.4.1"
exit 1
