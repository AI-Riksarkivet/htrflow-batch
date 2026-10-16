"""Workflow-shape gates for the release-on-merge pipeline."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
CI = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())


def _ifs(node: object):
    """Every `if:` expression in a loaded workflow, jobs and steps alike."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "if" and isinstance(value, str):
                yield value
            else:
                yield from _ifs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _ifs(item)


def test_a_pull_request_checks_dispatch_skips_what_a_pull_request_skips():
    # PyYAML reads the bare key `on` as the boolean True.
    inputs = CI[True]["workflow_dispatch"]["inputs"]
    assert inputs["pull_request_checks"]["type"] == "boolean"
    assert inputs["pull_request_checks"]["default"] is False

    pr_ifs = [e for e in _ifs(CI["jobs"]) if "pull_request" in e]
    assert pr_ifs, "ci.yml no longer skips anything on a pull request"
    for expr in pr_ifs:
        assert "inputs.pull_request_checks" in expr, expr
