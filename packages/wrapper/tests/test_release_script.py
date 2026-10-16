"""scripts/release.py: what main needs next (`state`) and the release's file
edits (`bump`). Every test works on a copy of the files it touches."""

from __future__ import annotations

import re
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
PIN_FILES = sorted(
    {p for ps in release.PINNED.values() for p in ps} | set(release.CONVERTER_REF_FILES)
)
SHA = "a" * 40
INIT_CMD = ["uv", "run", "--no-sync", "htrflow-campaigns", "init"] + [
    "--force",
    "examples/campaigns",
]
CONFIG_REF_CMD = ["uv", "run", "--no-sync", "python", "scripts/config_reference.py"]

DIGESTS = {
    name: "sha256:" + c * 64 for name, c in zip(("wrapper", "web", "campaigns"), "123")
}


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    for rel in {*FILES, *PIN_FILES}:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    return tmp_path


def no_lock(*args, **kwargs):
    return subprocess.CompletedProcess(args, 0)


def fake_run(*args, **kwargs):
    fake_run.calls.append(args[0])
    return subprocess.CompletedProcess(args, 0)


@pytest.fixture(autouse=True)
def _reset_calls():
    fake_run.calls = []


def bump(root: Path, new: str, chart: str) -> list[Path]:
    return release.bump(root, new, chart, run=no_lock)


def test_state_is_released_when_the_version_has_a_tag(repo_copy):
    assert release.state(repo_copy, {"v" + release.version(repo_copy)}) == "released"


def test_state_is_publish_right_after_a_bump(repo_copy):
    bump(repo_copy, "99.0.0", "99.0.0")
    assert release.state(repo_copy, set()) == "publish"


def test_state_is_tag_once_the_hook_pins_this_versions_digest(repo_copy):
    bump(repo_copy, "99.0.0", "99.0.0")
    release.pin(repo_copy, "99.0.0", DIGESTS, SHA, run=fake_run)
    assert release.state(repo_copy, set()) == "tag"


def pinned_hook(root: Path, version: str) -> list[str]:
    """Write the hook as `pin` leaves it: three digest lines, `# v<version>`."""
    hook = root / release.HOOK
    text = re.sub(
        rf"^(\s*image: {re.escape(release.CONVERTER_IMAGE)})\S*.*$",
        rf"\1@sha256:{'e' * 64}  # v{version}",
        hook.read_text(),
        flags=re.M,
    )
    hook.write_text(text)
    return text.splitlines(keepends=True)


def test_state_is_tag_for_a_hook_pinned_by_digest_at_this_version(repo_copy):
    pinned_hook(repo_copy, release.version(repo_copy))
    assert release.state(repo_copy, set()) == "tag"


def test_state_is_publish_when_the_hook_pins_an_older_release(repo_copy):
    version = release.version(repo_copy)
    lines = pinned_hook(repo_copy, version)
    i = next(i for i, line in enumerate(lines) if f"# v{version}" in line)
    lines[i] = lines[i].replace(f"# v{version}", "# v0.0.1")
    (repo_copy / release.HOOK).write_text("".join(lines))
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
    app = release.version(repo_copy)
    chart = re.search(r"^version: (\S+)$", before, re.M)[1]
    bump(repo_copy, "99.0.0", "99.1.0")
    after = (repo_copy / CHART).read_text()
    assert (
        before.replace(f'appVersion: "{app}"', 'appVersion: "99.0.0"').replace(
            f"\nversion: {chart}\n", "\nversion: 99.1.0\n"
        )
        == after
    )


def test_bump_writes_nothing_when_a_file_does_not_match(repo_copy):
    hook = repo_copy / release.HOOK
    hook.write_text(
        hook.read_text().replace(
            "image: docker.io/riksarkivet/htrflow-campaigns", "image: x", 1
        )
    )
    before = {rel: (repo_copy / rel).read_text() for rel in FILES}
    with pytest.raises(SystemExit):
        bump(repo_copy, "99.0.0", "99.1.0")
    assert {rel: (repo_copy / rel).read_text() for rel in FILES} == before


def test_bump_refuses_a_version_that_is_not_newer(repo_copy):
    with pytest.raises(SystemExit):
        bump(repo_copy, "0.0.1", "0.0.1")


def test_pin_writes_every_pin_the_last_release_commit_wrote(repo_copy):
    release.bump(repo_copy, "99.0.0", "99.0.0", run=no_lock)
    release.pin(repo_copy, "99.0.0", DIGESTS, SHA, run=fake_run)
    compose = (repo_copy / ".docker/docker-compose.yml").read_text()
    web = DIGESTS["web"]
    assert compose.count(f"riksarkivet/htrflow-web@{web}}}  # v99.0.0") == 2
    assert f"riksarkivet/htrflow-batch@{DIGESTS['wrapper']}}}  # v99.0.0" in compose
    values = (repo_copy / "charts/htrflow-batch/values.yaml").read_text()
    assert f"image: docker.io/riksarkivet/htrflow-web@{web}\n" in values
    assert "the published v99.0.0 web image" in values
    demo = (repo_copy / release.PINNED["wrapper"][0]).read_text()
    assert f"image: docker.io/riksarkivet/htrflow-batch@{DIGESTS['wrapper']}\n" in demo
    hook = (repo_copy / release.HOOK).read_text()
    assert hook.count(f"htrflow-campaigns@{DIGESTS['campaigns']}  # v99.0.0") == 3
    for f in release.CONVERTER_REF_FILES:
        assert f"CONVERTER_REF: {SHA}   # v99.0.0" in (repo_copy / f).read_text()
    assert fake_run.calls == [INIT_CMD, CONFIG_REF_CMD]


def test_pin_is_idempotent(repo_copy):
    release.bump(repo_copy, "99.0.0", "99.0.0", run=no_lock)
    release.pin(repo_copy, "99.0.0", DIGESTS, SHA, run=fake_run)
    once = {rel: (repo_copy / rel).read_text() for rel in PIN_FILES}
    release.pin(repo_copy, "99.0.0", DIGESTS, SHA, run=fake_run)
    assert {rel: (repo_copy / rel).read_text() for rel in PIN_FILES} == once


@pytest.mark.parametrize(
    ("digests", "ref"),
    [
        ({**DIGESTS, "web": "sha256:abc"}, SHA),
        ({**DIGESTS, "web": "sha256:" + "G" * 64}, SHA),
        ({k: v for k, v in DIGESTS.items() if k != "web"}, SHA),
        (DIGESTS, "abc123"),
    ],
)
def test_pin_refuses_malformed_input_and_writes_nothing(repo_copy, digests, ref):
    before = {rel: (repo_copy / rel).read_text() for rel in PIN_FILES}
    with pytest.raises(SystemExit):
        release.pin(repo_copy, "99.0.0", digests, ref, run=fake_run)
    assert {rel: (repo_copy / rel).read_text() for rel in PIN_FILES} == before
    assert fake_run.calls == []


# What a CI container's copy of the repo carries that git would not list
# (its `.git` is excluded and `uv sync` adds a `.venv`).
UNTRACKED = {".git", ".venv", ".worktrees", ".superpowers", "node_modules", "site"}
UNTRACKED |= {".cache", "__pycache__", ".pytest_cache"}


def tracked_files() -> list[str]:
    """The repo's own files: `git ls-files`, or a walk where there is no git."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
        )
        files = out.stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [
            p.relative_to(ROOT).as_posix()
            for p in ROOT.rglob("*")
            if not UNTRACKED & set(p.relative_to(ROOT).parts)
        ]
    return [
        f
        for f in files
        if not f.startswith(("docs/superpowers/", "docs/audits/"))
        and "tests" not in Path(f).parts[:-1]
        and (ROOT / f).is_file()
    ]


PIN = re.compile(
    r"riksarkivet/htrflow-(?:batch|web|campaigns)@sha256:(?!0{64})[0-9a-f]{64}"
    r"|CONVERTER_REF\W+[\"']?[0-9a-f]{40}"
)
# A captured e2e transcript (local registry and ghcr.io refs): history, not a pin.
TRANSCRIPTS = {"docs/development/e2e-indexed-jobs.md"}
D = "sha256:" + "a" * 64
S = "b" * 40


@pytest.mark.parametrize(
    ("line", "is_pin"),
    [
        (f"image: riksarkivet/htrflow-web@{D}", True),
        (f"image: docker.io/riksarkivet/htrflow-web@{D}", True),
        (f"image: ${{X:-riksarkivet/htrflow-batch@{D}}}", True),
        (f"  CONVERTER_REF: {S}   # v1.0.0", True),
        (f"- CONVERTER_REF: {S}", True),
        (f'CONVERTER_REF: "{S}"', True),
        (f"CONVERTER_REF={S}", True),
        ("image: riksarkivet/htrflow-web@sha256:1fbabef5...", False),
        ("image: riksarkivet/htrflow-web@sha256:" + "0" * 64, False),
        ("CONVERTER_REF: abc123", False),
    ],
)
def test_pin_pattern(line, is_pin):
    assert bool(PIN.search(line)) is is_pin


def test_every_release_pin_in_the_tree_is_owned_by_release_py():
    """No file pins a release image (full digest, as `pin` writes it) or
    CONVERTER_REF unless `pin` writes or regenerates it. A digest of zeros is
    a placeholder, and prose that abbreviates a digest is not a pin."""
    owned = {p for ps in release.PINNED.values() for p in ps}
    owned |= set(release.CONVERTER_REF_FILES)
    unowned = [
        f
        for f in tracked_files()
        if PIN.search((ROOT / f).read_text(errors="ignore"))
        and f not in owned | TRANSCRIPTS
        and not f.startswith(release.GENERATED)
    ]
    assert unowned == []


def test_published_go_pins_are_a_subset_of_pinned():
    go = (ROOT / ".dagger/published.go").read_text()
    block = re.search(r"publishedPins = map\[string\]string\{(.*?)\}", go, re.S)[1]
    paths = re.findall(r'"([^"]+/[^"]+)"', block)
    assert len(paths) == 3
    owned = {p for ps in release.PINNED.values() for p in ps}
    assert set(paths) <= owned


def test_the_version_command_prints_the_wrappers_version(capsys):
    """publish.yml's plan job reads the release's version through this, so
    the workflow and `state` read it from the same file the same way."""
    release.main(["version"])
    assert capsys.readouterr().out == release.version(ROOT) + "\n"


def test_pin_prints_only_the_paths_it_changed(repo_copy):
    """publish.yml stages exactly what `pin` printed, so the programs it runs
    write to stderr, never into that list."""
    kwargs = []
    release.pin(
        repo_copy,
        "99.0.0",
        DIGESTS,
        SHA,
        run=lambda *a, **kw: kwargs.append(kw) or subprocess.CompletedProcess(a, 0),
    )
    assert len(kwargs) == 2
    assert all(kw.get("stdout") is sys.stderr for kw in kwargs)
