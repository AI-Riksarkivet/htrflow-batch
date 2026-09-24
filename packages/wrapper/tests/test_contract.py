"""The fixture the frontend reads the wrapper's own output from (2026-09-23
test audit).

`scripts/wrapper_contract.py` writes what the wrapper produces -- a volume's
manifest.json and the termination messages of the failures the campaign page
has sentences for -- into `frontend/src/lib/fixtures/`, where the run
viewer's schema and `reasons.ts` are tested against it. This is the other
half: a format changed here makes the committed fixture stale, and that
fails here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from wrapper_contract import FIXTURE, build  # noqa: E402


def test_the_committed_fixture_is_what_the_wrapper_writes():
    assert json.loads(FIXTURE.read_text()) == build(), (
        f"{FIXTURE.relative_to(ROOT)} is not what scripts/wrapper_contract.py "
        "prints — run `make wrapper-contract`"
    )


def test_the_fixture_covers_what_the_page_has_sentences_for():
    """One ok page and one short message would pass for ever: these are the
    branches the frontend reads differently."""
    doc = build()
    statuses = {r["status"] for r in doc["manifest"]["results"].values()}
    assert statuses == {"ok", "failed", "skipped"}
    assert any("error" in r for r in doc["manifest"]["results"].values())
    terminations = doc["terminations"]
    clipped = [
        k for k, t in terminations.items() if t["error"].endswith("...(truncated)")
    ]
    assert sorted(clipped) == ["verifyAllFailedClipped", "verifyMissingClipped"]
    assert terminations["sigterm"]["error"] == "SIGTERM"
    assert terminations["verifyMissing"]["error"].startswith("verify failed: 2 missing")
    assert terminations["verifyAllFailed"]["error"].startswith("verify failed: all 3 ")
