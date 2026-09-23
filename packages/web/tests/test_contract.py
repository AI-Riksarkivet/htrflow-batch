"""The fixture the frontend's schemas are checked against (2026-09-14 audit).

`scripts/api_contract.py` prints what the routes answer -- the app itself,
asked over HTTP -- into `frontend/src/lib/fixtures/`, where
`api-contract.test.ts` parses every row with `jobSummarySchema` and
`jobDetailSchema`. This is the other half: a field renamed in the API makes
the committed fixture stale, and that fails here.
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
    assert {"done", "failed", "unknown"} <= states
    assert any(v["sourceUrl"] is None for d in doc["details"] for v in d["volumes"])
    assert any(v["progress"] is not None for d in doc["details"] for v in d["volumes"])


def test_the_fixture_carries_what_the_routes_add_to_the_projection():
    """Built from the projection functions, the fixture had no header, no
    version, no error body and no warm-up the route matched itself
    (2026-09-23 test audit); built through the app, it has them all."""
    doc = build()
    assert doc["reapedTotal"] == "2"
    assert doc["version"] == {"version": "v0.0.0-contract", "web": "0.0.0"}
    assert [(e["status"], sorted(e["body"])) for e in doc["errors"]] == [
        (404, ["detail"]),
        (502, ["detail"]),
        (503, ["detail"]),
    ]
    warmups = {row["name"]: row["warmup"] for row in doc["summaries"]}
    assert warmups == {
        "kyrk": {"phase": "running"},
        "gamla": {"phase": "succeeded"},
        "okand": {
            "phase": "failed",
            "reason": {"stage": "warmup", "permanent": True, "error": "bad model id"},
        },
    }
