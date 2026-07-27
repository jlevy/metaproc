"""Tests for engine.input_validation — process-level inputs validation.

Phase 2A of the process-file-design-cleanups spec. Validates operator-supplied
parameters and file-backed inputs at run-process startup.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from metaproc.engine.input_validation import validate_process_inputs
from metaproc.models.authored import ProcessSpec


def _spec_with_inputs(inputs: dict[str, object]) -> ProcessSpec:
    return ProcessSpec.model_validate({"name": "test", "inputs": inputs})


class TestParamInputs:
    def test_param_satisfied(self):
        spec = _spec_with_inputs({"tickers": {"param": "TICKERS", "as": "string"}})
        errors = validate_process_inputs(spec, variables={"TICKERS": "AAPL"}, process_dir=Path())
        assert errors == []

    def test_param_missing(self):
        spec = _spec_with_inputs({"tickers": {"param": "TICKERS", "as": "string"}})
        errors = validate_process_inputs(spec, variables={}, process_dir=Path())
        assert len(errors) == 1
        assert "TICKERS" in errors[0]


class TestFileInputs:
    def test_yaml_file_ok(self, tmp_path: Path):
        target = tmp_path / "kb.yaml"
        target.write_text("version: 1\n")
        spec = _spec_with_inputs(
            {
                "kb_index": {
                    "path": str(target),
                    "parse": {"format": "yaml"},
                    "as": "map",
                }
            }
        )
        errors = validate_process_inputs(spec, variables={}, process_dir=tmp_path)
        assert errors == []

    def test_missing_file(self, tmp_path: Path):
        spec = _spec_with_inputs(
            {
                "kb_index": {
                    "path": str(tmp_path / "missing.yaml"),
                    "parse": {"format": "yaml"},
                    "as": "map",
                }
            }
        )
        errors = validate_process_inputs(spec, variables={}, process_dir=tmp_path)
        assert len(errors) == 1
        assert "missing.yaml" in errors[0]

    def test_shape_mismatch(self, tmp_path: Path):
        target = tmp_path / "list.yaml"
        target.write_text("- a\n- b\n")
        spec = _spec_with_inputs(
            {
                "kb_index": {
                    "path": str(target),
                    "parse": {"format": "yaml"},
                    "as": "map",
                }
            }
        )
        errors = validate_process_inputs(spec, variables={}, process_dir=tmp_path)
        assert len(errors) == 1
        assert "expected map" in errors[0]

    def test_path_template_substitution(self, tmp_path: Path):
        target = tmp_path / "kb.yaml"
        target.write_text("version: 1\n")
        spec = _spec_with_inputs(
            {
                "kb_index": {
                    "path": "{{RUNS_DIR}}/kb.yaml",
                    "parse": {"format": "yaml"},
                    "as": "map",
                }
            }
        )
        errors = validate_process_inputs(
            spec,
            variables={"RUNS_DIR": str(tmp_path)},
            process_dir=tmp_path,
        )
        assert errors == []

    def test_frontmatter_extract(self, tmp_path: Path):
        target = tmp_path / "items.md"
        target.write_text(
            textwrap.dedent("""\
                ---
                progress:
                  schema: metaproc:ProgressSpec/0.1
                  process: test
                  run_id: r1
                  items:
                    - ticker: AAPL
                ---
                """)
        )
        spec = _spec_with_inputs(
            {
                "tickers": {
                    "path": str(target),
                    "parse": {"format": "frontmatter-md", "extract": "items"},
                    "as": "list<map>",
                }
            }
        )
        errors = validate_process_inputs(spec, variables={}, process_dir=tmp_path)
        assert errors == []


class TestMalformedInput:
    def test_neither_param_nor_path_is_invalid(self):
        spec = _spec_with_inputs({"bad": {"as": "string"}})
        errors = validate_process_inputs(spec, variables={}, process_dir=Path())
        assert len(errors) == 1
        assert "bad" in errors[0]


class TestNoInputsDeclared:
    def test_empty_inputs_no_errors(self):
        spec = ProcessSpec.model_validate({"name": "test"})
        errors = validate_process_inputs(spec, variables={}, process_dir=Path())
        assert errors == []


class TestMultipleInputs:
    def test_reports_all_errors(self, tmp_path: Path):
        spec = _spec_with_inputs(
            {
                "tickers": {"param": "TICKERS", "as": "string"},
                "kb_index": {
                    "path": str(tmp_path / "missing.yaml"),
                    "parse": {"format": "yaml"},
                    "as": "map",
                },
            }
        )
        errors = validate_process_inputs(spec, variables={}, process_dir=tmp_path)
        assert len(errors) == 2
