"""Distribution contract for MetaBrowser assets shipped in the Metaproc wheel."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

WHEEL_BUILD_TIMEOUT_SECONDS = 60


def test_wheel_contains_one_copy_of_every_plugin_asset(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "uv",
            "--no-config",
            "build",
            "--package",
            "metaproc",
            "--wheel",
            "--out-dir",
            str(tmp_path),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=WHEEL_BUILD_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    wheels = list(tmp_path.glob("metaproc-*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        members = archive.namelist()
        assert len(members) == len(set(members)), "wheel contains duplicate archive members"
        plugin_prefix = "metaproc/metabrowser_plugin/plugin/"
        plugin_assets = {
            member.removeprefix(plugin_prefix)
            for member in members
            if member.startswith(plugin_prefix)
        }

    assert plugin_assets == {
        "domain_views.css",
        "domain_views.js",
        "elk.bundled.js",
        "elkjs-license.txt",
        "index.js",
        "manifest.toml",
        "styles.css",
        "viz.css",
        "viz.js",
    }
