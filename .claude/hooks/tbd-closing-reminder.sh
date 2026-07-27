#!/bin/bash
# Remind about close protocol after git push
# Installed by: tbd setup --auto

if ! command -v jq &> /dev/null; then
  exit 0
fi

input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // empty')

# Check if this is a git push command and .tbd exists
if [[ "$command" == git\ push* ]] || [[ "$command" == *"&& git push"* ]] || [[ "$command" == *"; git push"* ]]; then
  # The hook may start in a subdirectory; check .tbd at the repo root.
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null) && cd "$repo_root"
  if [ -d ".tbd" ]; then
    export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"
    if command -v tbd &> /dev/null; then
      tbd closing
    fi
  fi
fi

exit 0
