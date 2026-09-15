"""No workflow leaves a credential in the checkout.

`actions/checkout` writes the job's token into `.git/config` as an
`extraheader` by default, where it outlives the step: every later step, and
anything those steps run -- a build script, a dependency's install hook, a
dagger container mounting the repo -- can push with it. Nothing in this
repository pushes from a workflow, so nothing needs it.

Five of the seven workflows already said `persist-credentials: false`; this
is the rule that keeps the next one from forgetting, and covers every
workflow rather than the two the audit happened to catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

#: GitHub reads both spellings out of this directory, so a rule that globs
#: one of them is a rule the next workflow can be written straight past.
_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"
WORKFLOWS = sorted(p for p in _DIR.iterdir() if p.suffix in (".yml", ".yaml"))


def _checkout_steps(workflow: dict) -> list[tuple[str, dict]]:
    return [
        (name, step)
        for name, job in workflow["jobs"].items()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    ]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_checkout_drops_the_token(path: Path) -> None:
    workflow = yaml.safe_load(path.read_text())
    steps = _checkout_steps(workflow)
    assert steps, f"{path.name} checks out nothing — did the step move?"
    for job, step in steps:
        assert step.get("with", {}).get("persist-credentials") is False, (
            path.name,
            job,
        )
