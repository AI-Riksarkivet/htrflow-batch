"""The Dependabot auto-merge workflow merges only what a test can vouch for.

Auto-merge waits for main's required checks, so what may auto-merge is what
those checks prove: patch and minor versions of the ecosystems pull-request
CI builds and tests. Docker base images are left out (pull-request CI does
not build the amd64 wrapper or the web image), and so is every major version.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = _ROOT / ".github" / "workflows" / "dependabot-automerge.yml"
DEPENDABOT = _ROOT / ".github" / "dependabot.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _merge_step() -> dict:
    (job,) = _load()["jobs"].values()
    (step,) = [s for s in job["steps"] if "gh pr merge" in s.get("run", "")]
    return step


def test_it_runs_on_pull_request_never_pull_request_target() -> None:
    triggers = _load()[True]  # PyYAML reads the bare `on:` key as True
    assert set(triggers) == {"pull_request"}


def test_only_dependabot_pull_requests_and_scoped_write() -> None:
    wf = _load()
    assert wf["permissions"] == {}
    (job,) = wf["jobs"].values()
    assert job["if"] == "github.event.pull_request.user.login == 'dependabot[bot]'"
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}
    for step in job["steps"]:
        uses = step.get("uses", "")
        assert not uses.startswith("actions/checkout@"), "it must not check out PR code"
        if uses:
            ref = uses.split("@", 1)[1]
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses


def test_it_merges_only_patch_and_minor_of_tested_ecosystems() -> None:
    cond = _merge_step()["if"]
    assert "version-update:semver-major" not in cond
    for update in ("version-update:semver-patch", "version-update:semver-minor"):
        assert update in cond
    for ecosystem in ("github_actions", "uv", "bun"):
        assert f'"{ecosystem}"' in cond
    for manual in ("docker", "gomod"):
        assert f'"{manual}"' not in cond
    assert "--auto" in _merge_step()["run"]


def test_every_auto_merged_ecosystem_is_one_dependabot_updates() -> None:
    """The metadata names ecosystems with underscores where dependabot.yml
    uses dashes (github-actions -> github_actions)."""
    configured = {
        u["package-ecosystem"].replace("-", "_")
        for u in yaml.safe_load(DEPENDABOT.read_text())["updates"]
    }
    for ecosystem in ("github_actions", "uv", "bun"):
        assert ecosystem in configured
