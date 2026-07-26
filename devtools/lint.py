import subprocess
import sys
from pathlib import Path

from funlog import log_calls
from rich import get_console, reconfigure
from rich import print as rprint

SRC_PATHS = ["src", "tests", "devtools", "examples"]
DOC_PATHS = ["README.md"]
PLC0415_CHECKER = str(Path(__file__).with_name("check_plc0415_justifications.py"))
VENDORED_PATHS = ["src/metaproc/metabrowser_plugin/plugin/elk.bundled.js"]


reconfigure(emoji=not get_console().options.legacy_windows)


def main():
    check_mode = "--check" in sys.argv

    rprint()

    errcount = 0
    if check_mode:
        errcount += run(["codespell", f"--skip={','.join(VENDORED_PATHS)}", *SRC_PATHS, *DOC_PATHS])
        errcount += run(["ruff", "check", *SRC_PATHS])
        errcount += run(["ruff", "format", "--check", *SRC_PATHS])
    else:
        errcount += run(
            [
                "codespell",
                "--write-changes",
                f"--skip={','.join(VENDORED_PATHS)}",
                *SRC_PATHS,
                *DOC_PATHS,
            ]
        )
        errcount += run(["ruff", "check", "--fix", *SRC_PATHS])
        errcount += run(["ruff", "format", *SRC_PATHS])
    errcount += run(["python", PLC0415_CHECKER, *SRC_PATHS])
    errcount += run(["basedpyright", "--stats", *SRC_PATHS])

    rprint()

    if errcount != 0:
        rprint(f"[bold red]:x: Lint failed with {errcount} errors.[/bold red]")
    else:
        rprint("[bold green]:white_check_mark: Lint passed![/bold green]")
    rprint()

    return errcount


@log_calls(level="warning", show_timing_only=True)
def run(cmd: list[str]) -> int:
    rprint()
    rprint(f"[bold green]>> {' '.join(cmd)}[/bold green]")
    errcount = 0
    try:
        subprocess.run(cmd, text=True, check=True)
    except KeyboardInterrupt:
        rprint("[yellow]Keyboard interrupt - Cancelled[/yellow]")
        errcount = 1
    except subprocess.CalledProcessError as e:
        rprint(f"[bold red]Error: {e}[/bold red]")
        errcount = 1

    return errcount


if __name__ == "__main__":
    exit(main())
