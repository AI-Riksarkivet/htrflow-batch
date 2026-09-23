"""scripts/docs_lint.py, the gate that keeps the documentation site general.

A rule whose pattern matches nothing passes every page, and nothing would
say so: each rule is held to a line it must flag and a nearby line it must
leave alone (test audit gap: the linter had no tests of its own).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "docs_lint.py"

CASES = {
    "ids": ("Fixed in B63 with the converter.", "A B-tree keeps the index."),
    "dates": ("Measured on 2026-09-23.", "Dates are written YYYY-MM-DD."),
    "versions": ("Install chart 0.13.0 first.", "Allow 0.0.0.0/0 on port 443."),
    "hardware": (
        "Built on an arm64 runner.",
        "Built on a runner of each architecture.",
    ),
    "site": (
        "Open http://localhost:30800 in a browser.",
        "Open the web front in a browser.",
    ),
}


def _lint(tmp_path: Path, line: str) -> subprocess.CompletedProcess[str]:
    page = tmp_path / "page.md"
    page.write_text(f"# Page\n\n{line}\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(page)], capture_output=True, text=True
    )


@pytest.mark.parametrize("rule", list(CASES))
def test_each_rule_flags_its_case_and_passes_the_plain_one(tmp_path: Path, rule: str):
    hit, plain = CASES[rule]
    flagged = _lint(tmp_path, hit)
    assert flagged.returncode == 1, flagged.stdout + flagged.stderr
    assert f": {rule}: " in flagged.stdout + flagged.stderr
    clean = _lint(tmp_path, plain)
    assert clean.returncode == 0, clean.stdout + clean.stderr
