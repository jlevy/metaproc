"""Guard metaproc's domain boundary."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "metaproc"
COMMANDS_ROOT = SRC_ROOT / "commands"


def _python_modules() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_metaproc_has_no_eval_package() -> None:
    assert not (SRC_ROOT / "eval").exists()


def test_metaproc_does_not_import_domain_packages() -> None:
    offenders: list[tuple[Path, str]] = []
    for module in _python_modules():
        rel = module.relative_to(SRC_ROOT)
        for imported in _imported_modules(module):
            if imported == "example_workflow" or imported.startswith("example_workflow."):
                offenders.append((rel, imported))

    assert not offenders, "metaproc imports domain packages:\n" + "\n".join(
        f"  {rel}: {imported}" for rel, imported in offenders
    )


def test_metaproc_cli_has_no_earnings_eval_or_items_commands() -> None:
    cli_source = (SRC_ROOT / "cli.py").read_text()
    command_sources = "\n".join(path.read_text() for path in COMMANDS_ROOT.rglob("*.py"))
    source = f"{cli_source}\n{command_sources}"
    banned = [
        "dataset-to-progress",
        "eval-consensus",
        "eval-judge",
        "eval-judge-aggregate",
        "metaproc.commands.dataset_to_progress",
        "metaproc.commands.eval_consensus",
        "metaproc.commands.eval_judge",
        "metaproc.commands.eval_judge_aggregate",
    ]
    leaks = [token for token in banned if token in source]
    assert not leaks


def test_frontmatter_does_not_statically_own_earnings_eval_envelopes() -> None:
    source = (SRC_ROOT / "io" / "frontmatter.py").read_text()
    banned = ["scoreboard_models", "judge_verdicts_v2", "scoreboard_v2"]
    leaks = [token for token in banned if token in source]
    assert not leaks
