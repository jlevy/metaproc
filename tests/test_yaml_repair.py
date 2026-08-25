"""Tests for YAML frontmatter repair."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands import run_parallel, run_process
from metaproc.engine import yaml_repair
from metaproc.engine.yaml_repair import repair_frontmatter_file
from metaproc.io import fmf_read


class TestRepairFrontmatterFile:
    def test_repairs_inline_notes_with_colons(self, tmp_path: Path) -> None:
        """Values with unquoted colons mid-value should get quoted."""
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "record:\n"
            "  ticker: AAPL\n"
            "  detail: Strong beat (Note: actually Q1 not Q2)\n"
            "  fiscal_quarter: 2025-Q2\n"
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True
        content = record.read_text()
        # The value should now be quoted
        assert '"Strong beat (Note: actually Q1 not Q2)"' in content
        # Other lines should be unchanged
        assert "ticker: AAPL" in content
        assert "fiscal_quarter: 2025-Q2" in content

    def test_no_repair_needed(self, tmp_path: Path) -> None:
        """Valid YAML should not be modified."""
        record = tmp_path / "record.md"
        original = "---\nrecord:\n  ticker: AAPL\n  fiscal_quarter: 2025-Q2\n---\n# Body\n"
        record.write_text(original)
        repaired = repair_frontmatter_file(record)
        assert repaired is False
        assert record.read_text() == original

    def test_repairs_value_with_embedded_colon(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "record:\n"
            "  consensus_error_detail: Large beat suggests operational outperformance. NOTE: Data inconsistency - source shows eps_surprise of 0.04\n"
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True

        _, meta = fmf_read(record)
        assert meta is not None

    def test_repairs_plain_scalar_starting_with_quoted_phrase(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "alt_data_research:\n"
            "  ticker: OLLI\n"
            '  strongest_alt_signal: "Ollies near me" Google Trends accelerated into May.\n'
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True

        _, meta = fmf_read(record)
        assert meta is not None
        assert (
            meta["alt_data_research"]["strongest_alt_signal"]
            == '"Ollies near me" Google Trends accelerated into May.'
        )

    def test_repairs_nested_list_value_starting_with_quoted_phrase(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        record.write_text(
            "---\n"
            "alt_data_research:\n"
            "  ticker: M\n"
            "  source_gaps:\n"
            "    - source: google_trends_reimagine\n"
            '      gap: "Macy\'s Reimagine" is sparse across all windows.\n'
            "---\n"
            "# Body\n"
        )
        repaired = repair_frontmatter_file(record)
        assert repaired is True

        _, meta = fmf_read(record)
        assert meta is not None
        assert (
            meta["alt_data_research"]["source_gaps"][0]["gap"]
            == '"Macy\'s Reimagine" is sparse across all windows.'
        )

    def test_preserves_already_quoted_values(self, tmp_path: Path) -> None:
        record = tmp_path / "record.md"
        original = '---\nrecord:\n  detail: "Already quoted: with colon"\n---\n# Body\n'
        record.write_text(original)
        repaired = repair_frontmatter_file(record)
        assert repaired is False

    def test_preserves_yaml_comments(self, tmp_path: Path) -> None:
        """Comments with colons should not trigger repair."""
        record = tmp_path / "record.md"
        original = "---\nrecord:\n  ticker: AAPL  # Note: this is a comment\n---\n# Body\n"
        record.write_text(original)
        repaired = repair_frontmatter_file(record)
        assert repaired is False

    def test_returns_false_if_file_missing(self, tmp_path: Path) -> None:
        record = tmp_path / "nonexistent.md"
        repaired = repair_frontmatter_file(record)
        assert repaired is False

    def test_self_check_uses_ruamel_yaml_strictness(self, tmp_path: Path) -> None:
        """Regression for the fix parser harmonization: the self-check
        must use the same parser as the downstream validator (ruamel.yaml).

        Verify by reading the source: an old version of repair_frontmatter_file
        used PyYAML's yaml.safe_load for both the pre-check and the post-repair
        verification. The post-2026-05-21 fix uses _ruamel_safe_load. Without
        this harmonization, the operator sees \"Repaired YAML\" log lines
        followed by invalid_outputs failures (the document passed PyYAML but
        fails the validator).
        """

        src = inspect.getsource(yaml_repair.repair_frontmatter_file)
        # Pre-check + post-check should both go through ruamel via _ruamel_safe_load.
        assert "_ruamel_safe_load" in src, (
            "repair_frontmatter_file must use _ruamel_safe_load (not yaml.safe_load) "
            "so its parser tracks the downstream validator's strictness. See the fix."
        )
        # The bare PyYAML self-check is gone — both call sites must use ruamel.
        assert "yaml.safe_load" not in src, (
            "PyYAML's yaml.safe_load should not appear in repair_frontmatter_file — "
            "parser mismatch with downstream validator caused 2026-05-21 incident."
        )


# ── Which executors rewrite agent output ─────────────────────────

REWRITING_PASSES = frozenset(
    {"repair_frontmatter_file", "repair_declared_outputs", "conform_declared_outputs"}
)
"""Every spelling of "rewrite the artifact before validating it"."""

MALFORMED_FRONTMATTER = (
    "---\nrecord:\n  ticker: AAPL\n  detail: Strong beat (Note: actually Q1 not Q2)\n---\n# Body\n"
)
"""An unquoted colon mid-value: the failure mode `TestRepairFrontmatterFile` covers.

The repair pass rewrites this document, so a code branch that ran it would be
visible in the file rather than only in the call graph.
"""


def _selects_code_mode(test: ast.expr) -> bool:
    """``<x>.mode == "code"`` and not its negation.

    The operator has to be read, not just the operand: ``run_parallel`` guards
    its adapter setup with ``target.mode != "code"``, which names the same two
    values and is the agent path.
    """
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Attribute)
        and test.left.attr == "mode"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "code"
    )


def _code_branches(module: ModuleType) -> list[ast.stmt]:
    """Every subtree of ``module`` that runs a step in ``mode: code``.

    ``run_parallel`` reaches its branch through ``if target.mode == "code":``
    inside the command function; ``run_process`` splits the body out as
    ``_execute_code_step`` and dispatches to it from a branch of the same shape.
    Both spellings are collected so neither executor's layout is assumed.
    """
    tree = ast.parse(inspect.getsource(module))
    branches: list[ast.stmt] = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
            and node.name.endswith("_execute_code_step")
        )
        or (isinstance(node, ast.If) and _selects_code_mode(node.test))
    ]
    assert branches, (
        f"no mode:code branch found in {module.__name__} — this guard is reading a "
        "layout the executor no longer has, and is asserting nothing"
    )
    return branches


def _called_names(node: ast.AST) -> set[str]:
    """Callee names under ``node``, bare and dotted alike.

    ``conform_declared_outputs(...)`` and ``schema_conform.conform_declared_outputs(...)``
    are the same call site with different imports, so both spellings have to count.
    """
    names: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
    return names


class TestWhichExecutorsRewriteAgentOutput:
    """Repair and conform are scoped to agent-authored output, and the scoping is tested.

    Both passes exist because an agent hand-writes YAML with no serializer in the
    path, so one unquoted colon costs a document. A code handler builds its artifact
    from typed values through a real writer, so a document that will not parse there
    is a bug in the handler, wrong for every item rather than for this one, and
    rewriting it would launder a serializer defect into a clean run.

    Three angles, because no one of them is sufficient. The behavioral test is the
    one that would notice if the rule stopped holding for a real run; the two source
    assertions localize a break to a call site and run in milliseconds.
    """

    def test_a_code_handlers_unparsable_output_is_left_alone_and_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rule as an operator meets it: the item fails, the document is untouched.

        This is the assertion that survives a refactor of how the passes are called.
        Re-adding either pass to the code branch flips both halves at once — the run
        goes green and the file comes back quoted.
        """
        monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "0.1")
        source_path = tmp_path / "items.md"
        source_path.write_text(
            "---\nprogress:\n  process: code-branch\n  items:\n    - ticker: AAPL\n---\n"
        )
        emitted = tmp_path / "emitted.md"
        emitted.write_text(MALFORMED_FRONTMATTER)

        item_output = "{{run.dir}}/items/{{ticker}}/record.md"
        process_path = tmp_path / "code-branch.process.md"
        process_path.write_text(
            f"""---
process:
  name: code-branch
  steps:
    - id: emit
      mode: code
      command: /bin/sh -c 'mkdir -p "{{{{run.dir}}}}/items/{{{{ticker}}}}" && cp "{emitted}" "{item_output}"'
      inputs:
        tickers:
          path: "{source_path}"
          kind: file
          format: frontmatter-md
      outputs:
        record:
          path: "{item_output}"
          kind: file
          format: frontmatter-md
      for_each:
        over: tickers
        bind: ticker
        bind_fields: [ticker]
---
# code-branch
"""
        )

        result = CliRunner().invoke(
            app,
            [
                "run-parallel",
                str(process_path),
                "--step",
                "emit",
                "--items",
                "AAPL",
                "--no-retry",
                "--var",
                "RUN_ID=test-run",
                "--var",
                f"RUNS_DIR={tmp_path / 'runs'}",
            ],
        )

        written = tmp_path / "runs" / "test-run" / "items" / "AAPL" / "record.md"
        assert written.exists(), f"the handler never wrote its output: {result.output}"
        assert written.read_text() == MALFORMED_FRONTMATTER, (
            "a rewriting pass ran on the mode:code branch — the handler's document "
            "came back changed instead of failing validation"
        )
        assert result.exit_code != 0, (
            f"an unparsable code-handler output must fail its item: {result.output}"
        )

    def test_no_rewriting_pass_is_called_on_either_code_branch(self) -> None:
        """Which call sites exist, asserted where a reviewer can see the break.

        Positional rather than by name: a pass added to the code branch fails here
        however it is spelled, including through ``repair_declared_outputs``, which
        is the helper an author reaching for repair would find first.
        """
        for module in (run_parallel, run_process):
            for branch in _code_branches(module):
                offenders = _called_names(branch) & REWRITING_PASSES
                assert not offenders, (
                    f"{module.__name__} calls {', '.join(sorted(offenders))} on its mode:code "
                    f"branch at line {branch.lineno}; repair and conform belong on the "
                    "agent path only"
                )

    def test_the_agent_executors_rewrite_through_the_shared_helpers(self) -> None:
        """The other direction: neither executor may stop rewriting agent output.

        Both helpers resolve a declared output path the way validation does, so a
        call site that reaches past them names a different file than the validator.
        """
        for module in (run_parallel, run_process):
            source = inspect.getsource(module)
            missing = {
                name
                for name in ("repair_declared_outputs(", "conform_declared_outputs(")
                if name not in source
            }
            assert not missing, (
                f"{module.__name__} no longer calls {', '.join(sorted(missing))} — agent "
                "output must still be repaired and conformed before it is validated"
            )
            reaches_past = "repair_frontmatter_file" in source
            assert not reaches_past, (
                f"{module.__name__} calls repair_frontmatter_file directly; repair belongs "
                "on the agent path, through repair_declared_outputs"
            )
