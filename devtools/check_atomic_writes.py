"""Enforce that every truncating file write in `metaproc` names its contract.

Why this is its own check:

`tbd guidelines filesystem-rules` says every write to a path has exactly one
contract -- publish-replace, publish-create-only, append, live stream, or
private staging -- and that only the first two may be implemented as an
ordinary truncating write, which is to say: never, because both of them
require staging a temp file and committing it in one step. It also says the
rule has to be executable at the boundary where it applies, because a rule
that lives only in a document is one reviewer's attention away from decaying.

Ruff cannot express this. `Path.write_text` is a method on a value, not an
import, so `flake8-tidy-imports` cannot reach it, and no bundled rule knows
which paths in this package are staged temp files and which are published
destinations. Hence an AST check of our own, in the shape of the existing
`check_plc0415_justifications.py`.

What it flags: a truncating write (`write_text`, `write_bytes`, `open(...,
"w")`, `Path.open("w")`, `os.fdopen(..., "w")`) whose destination is not
already a private staging path.

What it does not flag: append mode, read mode, and writes to a name bound by
`atomic_output_file`, `temp_output_file`, `temp_output_dir`,
`TemporaryDirectory`, `NamedTemporaryFile`, or `mkdtemp` -- those are staging
by construction, and a write to a staged path is the atomic pattern working
as intended. Appends are deliberately out of scope: routing an append through
replace-the-whole-file loses the concurrency property that made append
correct, so this check must never nudge anyone toward that.

Escaping the rule is possible and is supposed to be visible:

    log_fh = log_path.open("w")  # write-contract: live-stream -- the operator tails this

The contract name must come from CONTRACTS below and must carry a reason
after ` -- `. Naming a contract is a design decision recorded at the site;
a bare suppression is not, which is why one is rejected. Deliberate
exceptions and tracked debt read differently on purpose -- debt carries a
bead id in its reason, so nothing sits here permanently without something
that will eventually remind someone it is here.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# The contracts a truncating write may claim. `filesystem-rules` owns the
# definitions; these are the names a suppression may use.
CONTRACTS = {
    # Output made available incrementally to a consumer that reads while the
    # producer is still writing -- an operator tailing a log, or a handle
    # handed to a long-running subprocess. Atomic publication would break it.
    "live-stream",
    # A file with no published destination: a staging or work file the check
    # cannot see is private, because the path arrives from elsewhere.
    "private-staging",
    # Destination created only if absent, committed with a primitive that
    # atomically refuses an existing target (`open("x")`, `O_EXCL`).
    "create-only",
    # A path owned and written by an external tool, where the write here only
    # seeds or clears what that tool then manages.
    "external-tool",
}

# Names that bind a staged, not-yet-published path. A write to one of these
# is the atomic pattern working, not a violation of it.
STAGING_BINDERS = {
    "atomic_output_file",
    "temp_output_file",
    "temp_output_dir",
    "TemporaryDirectory",
    "NamedTemporaryFile",
    "mkdtemp",
    "mkstemp",
}

TRUNCATING_METHODS = {"write_text", "write_bytes"}

SUPPRESSION = "# write-contract:"


@dataclass(frozen=True)
class Finding:
    path: Path
    lineno: int
    what: str
    line: str


def _is_truncating_mode(node: ast.expr | None) -> bool:
    """True when a mode argument opens a file for truncating write.

    Append (`a`) and exclusive-create (`x`) are different contracts with their
    own primitives, and read modes are not writes at all. A non-literal mode
    is not judged: the check reports what it can prove.
    """
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    mode = node.value
    return "w" in mode


class _Scanner(ast.NodeVisitor):
    """Collect truncating writes to paths that are not known staging names."""

    def __init__(self) -> None:
        self.staged: set[str] = set()
        self.findings: list[Finding] = []

    # `with atomic_output_file(dest) as tmp:` and friends bind a staged name.
    # So does `staged = tmp_dir / "part.yaml"` once `tmp_dir` is staged, which
    # is why assignment is tracked as well as the `with` binding.
    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and _callee_name(call.func) in STAGING_BINDERS:
                if item.optional_vars is not None:
                    self.staged.update(_bound_names(item.optional_vars))
        self.generic_visit(node)

    visit_AsyncWith = visit_With  # pyright: ignore[reportAssignmentType]

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_staging_expr(node.value):
            for target in node.targets:
                self.staged.update(_bound_names(target))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and self._is_staging_expr(node.value):
            self.staged.update(_bound_names(node.target))
        self.generic_visit(node)

    def _is_staging_expr(self, node: ast.expr) -> bool:
        """True when an assigned value is, or derives from, a staged path.

        A staging binder is not always used as a context manager:
        `TemporaryDirectory()` is sometimes held in a module-level variable so
        its lifetime outlives one block, and the paths under it are as private
        as those from a `with`.
        """
        if self._mentions_staged(node):
            return True
        return any(
            isinstance(child, ast.Call) and _callee_name(child.func) in STAGING_BINDERS
            for child in ast.walk(node)
        )

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call) -> None:
        func = node.func
        # `dest.write_text(...)` / `dest.write_bytes(...)`
        if isinstance(func, ast.Attribute) and func.attr in TRUNCATING_METHODS:
            if not self._mentions_staged(func.value):
                self._record(node, f"`.{func.attr}(...)` on a published path")
            return
        # `dest.open("w")`
        if isinstance(func, ast.Attribute) and func.attr == "open":
            mode = node.args[0] if node.args else _keyword(node, "mode")
            if _is_truncating_mode(mode) and not self._mentions_staged(func.value):
                self._record(node, '`.open("w", ...)` on a published path')
            return
        # `open(dest, "w")` and `os.fdopen(fd, "w")`
        name = _callee_name(func)
        if name in {"open", "fdopen"}:
            mode = node.args[1] if len(node.args) > 1 else _keyword(node, "mode")
            if not _is_truncating_mode(mode):
                return
            # `os.fdopen` writes to an already-open descriptor, so the safety
            # question was settled at the `os.open` that produced it. Judge it
            # by the descriptor's name rather than guessing at the syscall.
            target = node.args[0] if node.args else None
            if target is not None and self._mentions_staged(target):
                return
            self._record(node, f'`{name}(..., "w")` on a published path')

    def _mentions_staged(self, node: ast.expr) -> bool:
        """True when a staged name appears anywhere in the expression.

        `tmp_path`, `tmp_path / "x.yaml"`, and `Path(tmp_path)` are all the
        same staged destination, so the test is containment rather than an
        exact name match.
        """
        return any(
            isinstance(child, ast.Name) and child.id in self.staged for child in ast.walk(node)
        )

    def _record(self, node: ast.Call, what: str) -> None:
        self.findings.append(Finding(Path(), node.lineno, what, ""))


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _bound_names(target: ast.expr) -> set[str]:
    """Every simple name bound by an assignment or `as` target."""
    return {child.id for child in ast.walk(target) if isinstance(child, ast.Name)}


# A contract reason that needs more than one line is the normal case, not the
# exception, so a marker is accepted anywhere in the comment block immediately
# above the call. The block is bounded so the search cannot wander into an
# unrelated comment further up the function.
MAX_COMMENT_BLOCK_LINES = 12


def _suppression(lines: list[str], lineno: int) -> str | None:
    """Return the contract text suppressing the finding, if there is one.

    Looked for in three places: on the statement's own first line, on its
    continuation lines (a call can span several), and anywhere in the
    unbroken run of comment lines directly above it.
    """
    for line in lines[lineno - 1 : lineno + 4]:
        if SUPPRESSION in line:
            return line.split(SUPPRESSION, 1)[1].strip()

    # Walk up through the contiguous comment block. A blank line or any code
    # ends it, which is what keeps this from reaching an unrelated marker.
    index = lineno - 2
    scanned = 0
    while index >= 0 and scanned < MAX_COMMENT_BLOCK_LINES:
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            break
        if SUPPRESSION in stripped:
            return stripped.split(SUPPRESSION, 1)[1].strip()
        index -= 1
        scanned += 1
    return None


def scan_file(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []

    scanner = _Scanner()
    scanner.visit(tree)

    lines = text.splitlines()
    findings: list[Finding] = []
    for finding in scanner.findings:
        line = lines[finding.lineno - 1] if finding.lineno <= len(lines) else ""
        claim = _suppression(lines, finding.lineno)
        if claim is not None:
            contract, _, reason = claim.partition("--")
            contract = contract.strip()
            if contract in CONTRACTS and reason.strip():
                continue
            if contract not in CONTRACTS:
                findings.append(
                    Finding(
                        path,
                        finding.lineno,
                        f"unknown write contract {contract!r} "
                        f"(expected one of: {', '.join(sorted(CONTRACTS))})",
                        line.rstrip(),
                    )
                )
                continue
            findings.append(
                Finding(
                    path,
                    finding.lineno,
                    f"write contract {contract!r} with no reason "
                    f"(expected `{SUPPRESSION} {contract} -- <reason>`)",
                    line.rstrip(),
                )
            )
            continue
        findings.append(Finding(path, finding.lineno, finding.what, line.rstrip()))
    return findings


def check_paths(roots: list[Path]) -> tuple[list[Finding], int]:
    """Scan every `.py` file under *roots*; return findings and the file count.

    The count is returned so the caller can refuse to pass vacuously. A check
    that reports success over zero files establishes nothing, and a moved
    directory or a typo'd path is exactly how that happens.
    """
    findings: list[Finding] = []
    scanned = 0
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for file in files:
            scanned += 1
            findings.extend(scan_file(file))
    return findings, scanned


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_atomic_writes.py <path>...", file=sys.stderr)
        return 2

    roots = [Path(arg) for arg in sys.argv[1:]]
    missing = [root for root in roots if not root.exists()]
    if missing:
        for root in missing:
            print(f"check_atomic_writes: no such path: {root}", file=sys.stderr)
        return 2

    findings, scanned = check_paths(roots)
    if scanned == 0:
        print(
            "check_atomic_writes: scanned 0 files -- the check would pass vacuously",
            file=sys.stderr,
        )
        return 2

    for finding in findings:
        print(f"{finding.path}:{finding.lineno}: {finding.what}", file=sys.stderr)
        print(f"    {finding.line}", file=sys.stderr)

    if findings:
        print(
            f"\n{len(findings)} unnamed truncating write(s) in {scanned} file(s).\n"
            "Publish completed files atomically -- `strif.atomic_write_text`, "
            "`atomic_write_bytes`, or `atomic_output_file(..., make_parents=True)` -- "
            "or name the contract in place:\n"
            f"    {SUPPRESSION} live-stream -- <why this must stay incremental>\n"
            f"Contracts: {', '.join(sorted(CONTRACTS))}. "
            "See `tbd guidelines filesystem-rules` and "
            "src/metaproc/docs/arch-file-io-utilities.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
