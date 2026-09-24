"""Dependabot covers every pin a registry can answer for, and only those.

A new composite action, dockerfile, lockfile or Go module that no update
entry reads is a pin that silently stops moving, so this walks the
repository for them rather than trusting the list in `.github/dependabot.yml`.
The htrflow base's lock is the one deliberate exception: it is htrflow's
pyproject.toml at HTRFLOW_REF plus the overlay, locked as a unit by
`make lock-htrflow-base`, and the image build refuses a pyproject.toml that
is not exactly that (docs/development/ci.md, "Dependency pins").
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CONFIG = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())
UPDATES = CONFIG["updates"]

#: Moved by hand, never by Dependabot (see the module docstring).
MANUAL = {"/.docker/htrflow-base"}

#: Checkouts, caches and build output hold lockfiles that are not ours.
_SKIP = {".git", ".venv", "node_modules", ".worktrees", "site", ".docs-site"}


def _dirs(ecosystem: str) -> list[str]:
    entries = [u for u in UPDATES if u["package-ecosystem"] == ecosystem]
    assert entries, ecosystem
    return [d for u in entries for d in u.get("directories", [u.get("directory")])]


def _where(directory: Path) -> str:
    """A directory as dependabot.yml spells it: rooted at the repository."""
    rel = directory.relative_to(REPO).as_posix()
    return "/" if rel == "." else f"/{rel}"


def _covered(ecosystem: str, directory: Path) -> bool:
    return any(fnmatch(_where(directory), glob) for glob in _dirs(ecosystem))


def _found(name: str) -> list[Path]:
    return [
        p for p in REPO.rglob(name) if not _SKIP.intersection(p.relative_to(REPO).parts)
    ]


def test_every_composite_action_is_updated() -> None:
    """This repository's own. The example campaigns repository has actions
    too, but `htrflow-campaigns init` writes them from the converter's
    templates, and a test holds the copy equal to what init writes."""
    actions = sorted((REPO / ".github" / "actions").glob("*/action.yml"))
    assert actions
    for action in actions:
        assert _covered("github-actions", action.parent), action


@pytest.mark.parametrize(
    ("ecosystem", "manifest"),
    [
        ("uv", "uv.lock"),
        ("bun", "bun.lock"),
        ("gomod", "go.mod"),
        ("docker", "*.dockerfile"),
    ],
)
def test_every_manifest_is_updated_or_named_manual(
    ecosystem: str, manifest: str
) -> None:
    found = _found(manifest)
    assert found, manifest
    for path in found:
        where = _where(path.parent)
        if where in MANUAL:
            assert not _covered(ecosystem, path.parent), where
        else:
            assert _covered(ecosystem, path.parent), path


@pytest.mark.parametrize("update", UPDATES, ids=lambda u: u["package-ecosystem"])
def test_every_update_is_labelled_grouped_and_cooled_down(update: dict) -> None:
    assert update["labels"] == ["dependencies"]
    assert update["commit-message"]["prefix"] == "chore(deps)"
    assert update["schedule"]["interval"] == "weekly"
    assert update["cooldown"]["default-days"] >= 7
    assert 0 < update["open-pull-requests-limit"] <= 5
    assert update["groups"], "one pull request per ecosystem, not one per package"
