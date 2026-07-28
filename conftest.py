import os

# Ensure Rich/Typer outputs plain text in CI (no ANSI escape codes).
# Must be set before Rich imports to affect console initialization.
if os.environ.get("CI"):
    os.environ.setdefault("NO_COLOR", "1")

# Skip the harness CLI --version pin checks at preflight / build_command time.
# The pins are a runtime drift guard against local/cloud version skew; tests
# never invoke the binaries, so the shell-out would only fail on CI runners
# that don't ship the CLIs.
os.environ.setdefault("METAPROC_SKIP_CLAUDE_VERSION_CHECK", "1")
os.environ.setdefault("METAPROC_SKIP_CODEX_VERSION_CHECK", "1")
os.environ.setdefault("METAPROC_SKIP_GEMINI_VERSION_CHECK", "1")
os.environ.setdefault("METAPROC_SKIP_PI_VERSION_CHECK", "1")

collect_ignore_glob = ["**/cloud/gcp/prefect_flow.py", "**/__main__.py"]
