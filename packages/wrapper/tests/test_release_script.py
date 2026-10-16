"""scripts/release.py: what main needs next (`state`) and the release's file
edits (`bump`). Every test works on a copy of the files it touches."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402

WRAPPER = "packages/wrapper/pyproject.toml"
WEB = "packages/web/pyproject.toml"
CONVERTER = "packages/converter/pyproject.toml"
CHART = "charts/htrflow-batch/Chart.yaml"
EXAMPLE = "examples/campaigns/argocd/apply.yaml"
FILES = [WRAPPER, WEB, CONVERTER, CHART, EXAMPLE, release.HOOK]

# Task 3 provides the real fixture; three valid digests are all `pin` needs.
DIGESTS = {
    name: "sha256:" + c * 64 for name, c in zip(("wrapper", "web", "converter"), "123")
}


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    for rel in FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    return tmp_path


def no_lock(*args, **kwargs):
    return subprocess.CompletedProcess(args, 0)


def bump(root: Path, new: str, chart: str) -> list[Path]:
    return release.bump(root, new, chart, run=no_lock)


def test_state_is_released_when_the_version_has_a_tag(repo_copy):
    assert release.state(repo_copy, {"v" + release.version(repo_copy)}) == "released"


def test_state_is_publish_right_after_a_bump(repo_copy):
    bump(repo_copy, "99.0.0", "99.0.0")
    assert release.state(repo_copy, set()) == "publish"


@pytest.mark.xfail(strict=True, reason="pin: Task 3")
def test_state_is_tag_once_the_hook_pins_this_versions_digest(repo_copy):
    bump(repo_copy, "99.0.0", "99.0.0")
    release.pin(repo_copy, "99.0.0", DIGESTS, "a" * 40)
    assert release.state(repo_copy, set()) == "tag"


def test_state_is_publish_when_the_hook_pins_an_older_release(repo_copy):
    # The checked-in hook pins the current release by digest; after a bump of
    # the version alone, with one hook line hand-edited back, it is not "tag".
    hook = repo_copy / release.HOOK
    version = release.version(repo_copy)
    lines = hook.read_text().splitlines(keepends=True)
    assert sum(f"# v{version}" in line for line in lines) == 3
    assert release.state(repo_copy, set()) == "tag"
    for i, line in enumerate(lines):
        if f"# v{version}" in line:
            lines[i] = line.replace(f"# v{version}", "# v0.0.1")
            break
    hook.write_text("".join(lines))
    assert release.state(repo_copy, set()) == "publish"


def test_bump_sets_every_version_and_the_hook_tag(repo_copy):
    changed = bump(repo_copy, "99.0.0", "99.1.0")
    for p in WRAPPER, WEB, CONVERTER:
        assert 'version = "99.0.0"' in (repo_copy / p).read_text()
    chart = (repo_copy / CHART).read_text()
    assert 'appVersion: "99.0.0"' in chart and "\nversion: 99.1.0\n" in chart
    assert (repo_copy / release.HOOK).read_text().count(
        "htrflow-campaigns:v99.0.0"
    ) == 3
    assert (repo_copy / EXAMPLE).read_text().count("htrflow-campaigns:v99.0.0") == 3
    assert "@sha256" not in (repo_copy / release.HOOK).read_text()
    assert {repo_copy / p for p in FILES} <= set(changed)


def test_bump_keeps_everything_else_byte_for_byte(repo_copy):
    before = (repo_copy / CHART).read_text()
    bump(repo_copy, "99.0.0", "99.1.0")
    after = (repo_copy / CHART).read_text()
    assert before.replace("0.12.0", "99.0.0").replace("0.18.0", "99.1.0") == after


def test_bump_refuses_a_version_that_is_not_newer(repo_copy):
    with pytest.raises(SystemExit):
        bump(repo_copy, "0.0.1", "0.0.1")
