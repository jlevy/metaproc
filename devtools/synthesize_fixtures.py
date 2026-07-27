"""Deterministically sanitize captured adapter fixtures for public tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOTS = (
    ROOT / "tests" / "fixtures" / "log_compaction",
    ROOT / "tests" / "fixtures" / "trace_agents",
)
TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
COMPONENT_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*")
HOME_PATH_PATTERN = re.compile(r"/(?:Users|home)/[^/\s\"']+")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ISO_DATE_PATTERN = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
CALL_ID_PATTERN = re.compile(r"\bcall_[A-Za-z0-9_-]+\b")
PRIVATE_IPV4_PATTERN = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)
TRACE_ENV_KEYS = frozenset(
    {
        "EXECUTION_PROFILE",
        "LANE_ID",
        "METAPROC_ITEM_KEY",
        "METAPROC_LANE_ID",
        "METAPROC_STEP",
        "METAPROC_VARIANT",
    }
)
PRIVATE_TOKEN_HASHES = frozenset(
    {
        "31523fdddcd5294e2bc6cc775fba79e2597e6d45bc0a995c548493038ebea33f",
        "505030f438f746617d86174f0a21b70997f9b84ae415c1e31708f91711e5f189",
        "0035e3bed3a10ebe81bc85bbf80cc092871ba344926efa491f195e36cc7e003b",
        "2d53000528ed0be7f64e8498c577123bb9bbbb560223db2e0f70dd690e2b1aa1",
        "b526066864bb66f02f74b77aca24fa328c325c6f6d71f6efbf9957b874490b55",
        "e498282813b5fd5e888a0b7ea0a2da392447b2f9bb3a3f8cecbec00c27a0f1b9",
        "96b5511d77cf16dc6d453c6275a0e3756f1893b425f8b4423e386aff6da4172d",
        "8964f8db8aa69294f076f9a4163ddf7b54bece9177f099c0b68cea159c07e656",
        "92d66e877d3142ea5b9ff267347b44a3d3a07fff6b666a84604e361555a6c98d",
        "f653a6c9123f6c82925db89bc474bb6642474d28e30fa7f75928e0ea48bfb302",
        "d875563e5fa8fff6c67736bdfa5b8b4e3c4cc624a562b6b35baae20b68a388c6",
        "0b26b4aef2cb20e29d2d121c67f109267ab87913e1cb2dc0f0ba07d91056e516",
        "4443ebe6787ef8c1d6c4be955c4f783171707c0d3a10bc19af8a8e98115f6244",
        "28afbf0889041b38ca15dfdb65e8bd7b657646923e806223b978f2a49cd66519",
    }
)
PROSE_KEYS = {
    "aggregated_output",
    "command",
    "content",
    "delta",
    "description",
    "message",
    "output",
    "prompt",
    "result",
    "strategic_intent",
    "summary",
    "text",
    "thinking",
    "title",
}


def _stable_label(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"synthetic-{kind}-{digest}"


def _replace_private_token(match: re.Match[str]) -> str:
    token = match.group(0)
    digest = hashlib.sha256(token.lower().encode()).hexdigest()
    return "synthetic" if digest in PRIVATE_TOKEN_HASHES else token


def sanitize_string(value: str, *, key: str = "") -> str:
    """Return a stable synthetic string while retaining parser-relevant shape."""
    normalized_key = key.lower()
    if normalized_key == "vars_json":
        return "{}"
    if normalized_key == "argv" and len(value) > 80:
        return "Synthetic fixture argument."
    if normalized_key in PROSE_KEYS:
        if normalized_key == "command":
            return "printf 'synthetic fixture output\\n'"
        return f"Synthetic fixture {normalized_key} content."
    if normalized_key in {
        "batch",
        "company",
        "customer",
        "key",
        "run_id",
        "step",
        "ticker",
        "tickers",
        "symbol",
    }:
        if value.startswith("synthetic-"):
            return value
        return _stable_label(normalized_key, value)
    if normalized_key in {
        "cwd",
        "filepath",
        "log_path",
        "path",
        "process_dir",
        "source_log",
    } or normalized_key.endswith(("_dir", "_file", "_path", "_root")):
        if value.startswith("/workspace/synthetic-project/"):
            return value
        suffix = Path(value).suffix or ".txt"
        return f"/workspace/synthetic-project/{_stable_label('file', value)}{suffix}"
    if normalized_key in {"branch", "git_branch", "run_branch"}:
        return "synthetic-branch"
    if normalized_key in {"logname", "user", "username"}:
        return "synthetic-user"

    value = HOME_PATH_PATTERN.sub("/workspace/synthetic-home", value)
    value = EMAIL_PATTERN.sub("synthetic-user@example.invalid", value)
    value = PRIVATE_IPV4_PATTERN.sub("192.0.2.10", value)
    value = ISO_DATE_PATTERN.sub("2030-01-01", value)
    value = CALL_ID_PATTERN.sub(lambda match: _stable_label("call", match.group()), value)
    value = TOKEN_PATTERN.sub(_replace_private_token, value)
    return COMPONENT_PATTERN.sub(_replace_private_token, value)


def sanitize_value(value: Any, *, key: str = "") -> Any:
    """Recursively sanitize JSON-compatible fixture data."""
    if isinstance(value, dict):
        if key.lower() == "env_redacted":
            value = {
                child_key: child_value
                for child_key, child_value in value.items()
                if str(child_key) in TRACE_ENV_KEYS
            }
        if key.lower() == "memory_paths":
            return {
                sanitize_string(str(child_key)): sanitize_value(child_value, key="path")
                for child_key, child_value in value.items()
            }
        return {
            sanitize_string(str(child_key)): sanitize_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item, key=key) for item in value]
    if isinstance(value, str):
        return sanitize_string(value, key=key)
    return value


def synthetic_bytes(path: Path) -> bytes:
    """Render the canonical synthetic representation for one fixture."""
    if path.suffix == ".jsonl":
        records: list[Any] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"type": "synthetic-raw", "text": line})
        return (
            "\n".join(
                json.dumps(sanitize_value(record), sort_keys=True, separators=(",", ":"))
                for record in records
            )
            + "\n"
        ).encode()
    if path.suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        return (json.dumps(sanitize_value(value), indent=2, sort_keys=True) + "\n").encode()
    return sanitize_string(path.read_text(encoding="utf-8")).encode()


def fixture_paths() -> list[Path]:
    """Return every checked-in synthetic fixture input in stable order."""
    return sorted(
        path
        for root in FIXTURE_ROOTS
        for path in root.rglob("*")
        if path.is_file() and path.name != "README.md"
    )


def run(*, check: bool) -> int:
    """Regenerate fixtures, or verify that regeneration is byte-identical."""
    changed: list[Path] = []
    for path in fixture_paths():
        rendered = synthetic_bytes(path)
        if path.read_bytes() == rendered:
            continue
        changed.append(path)
        if not check:
            path.write_bytes(rendered)
    if changed and check:
        for path in changed:
            print(path.relative_to(ROOT))
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return run(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
