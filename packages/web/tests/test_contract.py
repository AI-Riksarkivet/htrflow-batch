"""The fixture the frontend's schemas are checked against (2026-09-14 audit).

`scripts/api_contract.py` prints real projection output -- the same
functions the routes call -- into `frontend/src/lib/fixtures/`, where
`api-contract.test.ts` parses every row with `jobSummarySchema` and
`jobDetailSchema`. This is the other half: a field renamed in `projection.py`
makes the committed fixture stale, and that fails here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from api_contract import FIXTURE, build  # noqa: E402


def test_the_committed_fixture_is_what_the_api_prints():
    assert json.loads(FIXTURE.read_text()) == build(), (
        f"{FIXTURE.relative_to(ROOT)} is not what scripts/api_contract.py "
        "prints — run `make api-contract`"
    )


def test_the_fixture_covers_the_rows_the_page_has_to_draw():
    """A contract fixture of one happy row would pass for ever. These are
    the branches the schemas actually differ on."""
    doc = build()
    phases = {row["phase"] for row in doc["summaries"]}
    assert {"Running", "Succeeded", "Unknown"} <= phases
    assert {row["jobGone"] for row in doc["summaries"]} == {True, False}
    assert any(row["finishedAt"] is None for row in doc["summaries"])
    assert {row["jobGone"] for row in doc["details"]} == {True, False}
    for detail in doc["details"]:
        assert detail["volumes"], "a campaign with no rows proves nothing"
    states = {v["state"] for d in doc["details"] for v in d["volumes"]}
    assert {"done", "failed"} <= states
    assert any(v["sourceUrl"] is None for d in doc["details"] for v in d["volumes"])
    assert any(v["progress"] is not None for d in doc["details"] for v in d["volumes"])
