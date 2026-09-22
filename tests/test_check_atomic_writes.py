"""Contracts for the truncating-write check.

A lint floor that cannot demonstrate a rejection is indistinguishable from a lint floor
that is off, so these are mostly probe fixtures: each one is a snippet the check must
reject, or a snippet it must not. The two directions matter equally. A check that
flagged writes to staged temp paths would push contributors toward suppressing it, and
after that the boundary stops meaning anything.

The interesting behavior is what the check *ignores*: appends and writes to a path bound
by `atomic_output_file`. Routing an append through replace-the-whole-file loses the
concurrency property that made append correct, so a check that nudged anyone toward that
would be worse than no check.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from devtools.check_atomic_writes import CONTRACTS, check_paths, scan_file


def _module(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


REJECTED = [
    pytest.param('Path("out.yaml").write_text(body)', id="write_text"),
    pytest.param('Path("out.bin").write_bytes(blob)', id="write_bytes"),
    pytest.param("dest.write_text(body)", id="write_text-on-name"),
    pytest.param('with dest.open("w") as fh:\n    fh.write(body)', id="path-open-w"),
    pytest.param('with open(dest, "w") as fh:\n    fh.write(body)', id="builtin-open-w"),
    pytest.param('with open(dest, mode="w") as fh:\n    fh.write(body)', id="open-mode-kwarg"),
    pytest.param('with os.fdopen(fd, "w") as fh:\n    fh.write(body)', id="fdopen-w"),
    pytest.param('io.open(dest, "w")', id="qualified-open"),
    pytest.param('builtins.open(file=dest, mode="w")', id="qualified-open-keywords"),
    pytest.param(
        "def first():\n    with atomic_output_file(dest) as path:\n        pass\n"
        "def second(path):\n    path.write_text(body)",
        id="staging-does-not-leak-between-functions",
    ),
    pytest.param(
        "with atomic_output_file(dest) as path:\n    path = dest\n    path.write_text(body)",
        id="rebound-staging-name",
    ),
    pytest.param(
        "with atomic_output_file(dest) as path:\n    pass\npath.write_text(body)",
        id="expired-staging-context",
    ),
    pytest.param(
        "with atomic_output_file(dest) as tmp:\n    dest = choose_destination(tmp)\n"
        "    dest.write_text(body)",
        id="unknown-call-is-not-a-staged-path",
    ),
    pytest.param(
        'with temp_output_dir() as tmp:\n    (tmp / "/published").write_text(body)',
        id="absolute-path-discards-staging-root",
    ),
    pytest.param(
        'dest.write_text(body)\nlog.open("w")  # write-contract: live-stream -- tailed',
        id="later-statement-cannot-suppress-earlier-write",
    ),
    pytest.param(
        'dest.write_text("# write-contract: private-staging -- not a comment")',
        id="string-is-not-a-suppression",
    ),
]


@pytest.mark.parametrize("snippet", REJECTED)
def test_truncating_write_is_rejected(tmp_path: Path, snippet: str) -> None:
    findings = scan_file(_module(tmp_path, snippet))
    assert len(findings) == 1, f"expected one finding, got {findings}"
    assert "published path" in findings[0].what


ACCEPTED = [
    pytest.param(
        """
        with atomic_output_file(dest, make_parents=True) as tmp_path:
            tmp_path.write_text(body)
        """,
        id="staged-by-atomic_output_file",
    ),
    pytest.param(
        """
        with atomic_output_file(dest) as tmp_path:
            Path(tmp_path).write_text(body)
        """,
        id="staged-path-wrapped",
    ),
    pytest.param(
        """
        with temp_output_dir() as staging:
            part = staging / "part.yaml"
            part.write_text(body)
        """,
        id="staged-path-derived",
    ),
    pytest.param(
        """
        with dest.open("a") as fh:
            fh.write(line)
        """,
        id="append-is-its-own-contract",
    ),
    pytest.param(
        """
        with dest.open("xb") as fh:
            fh.write(blob)
        """,
        id="exclusive-create-is-its-own-contract",
    ),
    pytest.param('body = dest.read_text(encoding="utf-8")', id="read"),
]


@pytest.mark.parametrize("snippet", ACCEPTED)
def test_non_publication_write_is_accepted(tmp_path: Path, snippet: str) -> None:
    assert scan_file(_module(tmp_path, snippet)) == []


def test_named_contract_with_a_reason_is_accepted(tmp_path: Path) -> None:
    source = """
    log_fh = log_path.open("w")  # write-contract: live-stream -- the operator tails this
    """
    assert scan_file(_module(tmp_path, source)) == []


def test_bare_suppression_is_rejected(tmp_path: Path) -> None:
    """A contract with no reason is not a decision, so it does not count as one."""
    source = """
    log_fh = log_path.open("w")  # write-contract: live-stream
    """
    findings = scan_file(_module(tmp_path, source))
    assert len(findings) == 1
    assert "no reason" in findings[0].what


def test_unknown_contract_is_rejected(tmp_path: Path) -> None:
    """An invented contract name would let anyone opt out by making one up."""
    source = """
    log_fh = log_path.open("w")  # write-contract: because-i-said-so -- no
    """
    findings = scan_file(_module(tmp_path, source))
    assert len(findings) == 1
    assert "unknown write contract" in findings[0].what
    for contract in CONTRACTS:
        assert contract in findings[0].what


def test_contract_on_the_preceding_line_is_accepted(tmp_path: Path) -> None:
    """A long reason needs its own line, the same latitude the PLC0415 check gives."""
    source = """
    # write-contract: live-stream -- handed to a subprocess that writes for minutes
    log_fh = log_path.open("w")
    """
    assert scan_file(_module(tmp_path, source)) == []


def test_contract_anywhere_in_the_comment_block_above_is_accepted(tmp_path: Path) -> None:
    """A reason worth writing usually needs more room than one trailing comment."""
    source = """
    # write-contract: live-stream -- the operator tails this
    # The fd is the child's stdout, and the pool samples this file's size while
    # the step runs, so it has to grow incrementally.
    log_fh = log_path.open("w")
    """
    assert scan_file(_module(tmp_path, source)) == []


def test_a_blank_line_ends_the_comment_block(tmp_path: Path) -> None:
    """Otherwise one marker would silently cover every write below it."""
    source = """
    # write-contract: live-stream -- this belongs to some earlier statement

    dest.write_text(body)
    """
    findings = scan_file(_module(tmp_path, source))
    assert len(findings) == 1
    assert "published path" in findings[0].what


def test_code_between_the_marker_and_the_call_ends_the_block(tmp_path: Path) -> None:
    source = """
    # write-contract: live-stream -- this belongs to the open below
    log_fh = log_path.open("w")
    dest.write_text(body)
    """
    findings = scan_file(_module(tmp_path, source))
    assert len(findings) == 1
    assert findings[0].line.strip() == "dest.write_text(body)"


def test_multiline_call_finds_its_contract(tmp_path: Path) -> None:
    source = """
    log_fh = open(
        log_path,
        "w",
        encoding="utf-8",
    )  # write-contract: live-stream -- the operator tails this
    """
    assert scan_file(_module(tmp_path, source)) == []


def test_syntax_error_is_skipped_not_crashed(tmp_path: Path) -> None:
    """A file the parser cannot read is the formatter's problem, not this check's."""
    path = tmp_path / "broken.py"
    path.write_text("def f(:\n", encoding="utf-8")
    assert scan_file(path) == []


def test_scanning_nothing_is_reported(tmp_path: Path) -> None:
    """A check that passes over zero files establishes nothing.

    This is the failure mode a moved directory or a typo'd path produces, and it is
    silent by construction unless the count is asserted.
    """
    _, scanned = check_paths([tmp_path])
    assert scanned == 0


def test_the_package_has_no_unnamed_truncating_writes() -> None:
    """The live floor: every write in the shipped package names its contract.

    This is the assertion that makes the rule a gate rather than a document. It runs
    against the real tree, so a new unnamed write fails here before it reaches review.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "metaproc"
    findings, scanned = check_paths([src])
    assert scanned > 0, "the package source moved; this check was about to pass vacuously"
    assert findings == [], "\n".join(
        f"{f.path}:{f.lineno}: {f.what}\n    {f.line}" for f in findings
    )
