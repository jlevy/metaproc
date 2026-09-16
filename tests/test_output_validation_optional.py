"""An output a step need not write is absent without failing it."""

from pathlib import Path

from metaproc.engine.validation import validate_item_outputs_detailed
from metaproc.models.authored import IOSpec


def test_an_absent_optional_output_does_not_fail_the_step(tmp_path: Path) -> None:
    outputs = {
        "required_report": IOSpec(path=str(tmp_path / "report.md"), kind="file"),
        "staged_profile": IOSpec(path=str(tmp_path / "profile.md"), kind="file", optional=True),
    }
    (tmp_path / "report.md").write_text("# Report\n")

    assert validate_item_outputs_detailed(tmp_path, outputs) == []


def test_an_absent_required_output_still_fails(tmp_path: Path) -> None:
    outputs = {"required_report": IOSpec(path=str(tmp_path / "report.md"), kind="file")}

    failures = validate_item_outputs_detailed(tmp_path, outputs)

    assert [f.output for f in failures] == ["required_report"]


def test_a_present_optional_output_is_validated_like_any_other(tmp_path: Path) -> None:
    outputs = {
        "staged_profile": IOSpec(
            path=str(tmp_path / "profile.md"),
            kind="file",
            format="frontmatter-md",
            contract="metaproc:ProgressSpec/0.1",
            optional=True,
        )
    }
    (tmp_path / "profile.md").write_text("not a frontmatter document\n")

    failures = validate_item_outputs_detailed(tmp_path, outputs)

    assert [f.output for f in failures] == ["staged_profile"]


def test_an_absent_optional_directory_output_does_not_fail_the_step(tmp_path: Path) -> None:
    outputs = {
        "staged_bundles": IOSpec(path=str(tmp_path / "bundles"), kind="directory", optional=True)
    }

    assert validate_item_outputs_detailed(tmp_path, outputs) == []
