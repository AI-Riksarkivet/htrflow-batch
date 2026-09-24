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
    # A reaped campaign's detail is drawn from its record, and its latest
    # volume is a row the page renders: a fixture with only a live `latest`
    # would leave the reaped one's shape unchecked.
    assert any(d["jobGone"] and d["latest"] is not None for d in doc["details"])


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


def test_the_reaped_window_is_read_off_the_route():
    """The page asks for reaped campaigns a page at a time and never past the
    API's cap; the vitest holds its REAPED_PAGE/REAPED_MAX to these. They
    are what the route does -- the rows it sends unasked, the last
    ``?reaped=`` it answers -- so they are checked against its constants
    here only to prove the probe found the edge, not to restate them."""
    from htrflow_web import app

    assert build()["reapedLimits"] == {
        "default": app.REAPED_SHOWN,
        "max": app.REAPED_MAX,
    }


def test_only_the_scored_campaign_says_it_scores_page_quality():
    """kyrk's pipeline has a QualityPrediction step and its detail carries a
    quality block; the reaped campaigns' pipelines have neither."""
    doc = build()
    assert {r["name"]: r["qualityPrediction"] for r in doc["summaries"]} == {
        "kyrk": True,
        "gamla": False,
        "okand": False,
    }
    for d in doc["details"]:
        assert d["qualityPrediction"] is (d["name"] == "kyrk")
