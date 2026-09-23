from pathlib import Path

import pytest


@pytest.fixture
def commit_all():
    """``commit_all(repo)`` makes ``repo`` a git checkout of everything in
    it, one commit, and returns that commit's sha. Written by dulwich, the
    git the Argo CD hook's image has, so no git binary is needed."""
    from dulwich import porcelain

    def commit(repo: Path) -> str:
        porcelain.init(str(repo))
        porcelain.add(str(repo), [str(p) for p in repo.rglob("*") if p.is_file()])
        who = b"t <t@e>"
        return porcelain.commit(str(repo), b"x", author=who, committer=who).decode()

    return commit
