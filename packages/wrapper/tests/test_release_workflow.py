"""The release attaches signed chart packages, and nothing unsigned.

`release.yml` packages both charts at the tag, signs every file it attaches
(keyless cosign, one `<file>.sigstore.json` bundle each) and records their
SLSA provenance (`provenance.intoto.jsonl`) in a job of its own, and only then
does a second job create the release with them. Those two suffixes are the
ones OpenSSF Scorecard's Signed-Releases check reads a release's assets for.
The signing identity is this workflow at the tag, so the job holding it is
the only one with `id-token: write`, and the job that can write to the
repository holds nothing that signs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
GITHUB = REPO / ".github"
WORKFLOW = yaml.safe_load((GITHUB / "workflows" / "release.yml").read_text())
JOBS = WORKFLOW["jobs"]
#: PyYAML reads the bare key `on` as the boolean True.
TRIGGERS = WORKFLOW[True]


def _uses(steps: list[dict], prefix: str) -> int:
    found = [i for i, s in enumerate(steps) if s.get("uses", "").startswith(prefix)]
    assert len(found) == 1, prefix
    return found[0]


def _runs(steps: list[dict], needle: str) -> int:
    found = [i for i, s in enumerate(steps) if needle in s.get("run", "")]
    assert len(found) == 1, needle
    return found[0]


def test_permissions_are_granted_per_job_and_only_what_it_uses() -> None:
    assert WORKFLOW["permissions"] == {}
    assert JOBS["charts"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    assert JOBS["release"]["permissions"] == {"contents": "write"}
    assert JOBS["backfill"]["permissions"] == {"contents": "write"}


def test_every_action_in_every_workflow_is_pinned_by_commit_sha() -> None:
    """A tag can be moved to other code; a commit cannot. The version rides
    along as a comment, which is also what Dependabot updates beside it."""
    files = [
        *(GITHUB / "workflows").glob("*.yml"),
        *GITHUB.glob("actions/*/action.yml"),
    ]
    pinned = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")
    for path in files:
        for line in path.read_text().splitlines():
            found = re.match(r"^\s*(?:- )?uses:\s*(.+)$", line)
            if found and not found.group(1).startswith("./"):
                assert pinned.match(found.group(1)), (path.name, line.strip())


def test_every_attached_file_is_signed_before_the_release_exists() -> None:
    steps = JOBS["charts"]["steps"]
    package = _runs(steps, "package /charts/htrflow-batch /charts/htrflow-devstack")
    assert "sha256sum -- *.tgz > SHA256SUMS" in steps[package]["run"]
    sign = _runs(steps, "cosign sign-blob")
    assert _uses(steps, "sigstore/cosign-installer@") < sign
    assert steps[sign]["working-directory"] == "dist"
    assert "for file in *.tgz SHA256SUMS; do" in steps[sign]["run"]
    assert '--bundle "${file}.sigstore.json" "$file"' in steps[sign]["run"]
    provenance = _uses(steps, "actions/attest-build-provenance@")
    assert steps[provenance]["with"] == {"subject-checksums": "dist/SHA256SUMS"}
    copy = _runs(steps, "dist/provenance.intoto.jsonl")
    upload = _uses(steps, "actions/upload-artifact@")
    assert package < sign < provenance < copy < upload
    assert steps[upload]["with"]["path"] == "dist/"

    # Both jobs that attach need the signing job, so neither runs if it failed.
    for job, attach in (
        ("release", "gh release create"),
        ("backfill", "gh release upload"),
    ):
        assert JOBS[job]["needs"] == "charts"
        steps = JOBS[job]["steps"]
        download = _uses(steps, "actions/download-artifact@")
        assert steps[download]["with"] == {"name": "release-assets", "path": "dist"}
        assert download < _runs(steps, attach)
        assert "dist/*" in steps[_runs(steps, attach)]["run"]


def test_a_backfill_attaches_no_provenance_and_replaces_nothing() -> None:
    """Run from main, the provenance would name main's commit as the source
    of packages built from the tag."""
    steps = JOBS["charts"]["steps"]
    for i in (
        _uses(steps, "actions/attest-build-provenance@"),
        _runs(steps, "dist/provenance.intoto.jsonl"),
    ):
        assert steps[i]["if"] == "github.event_name == 'push'"
    upload = JOBS["backfill"]["steps"][-1]["run"]
    assert "--clobber" not in upload
    assert JOBS["backfill"]["if"] == "github.event_name == 'workflow_dispatch'"
    assert JOBS["release"]["if"] == "github.event_name == 'push'"


def test_signing_runs_only_as_an_identity_the_recipe_accepts() -> None:
    """The verify recipe names release.yml at the tag or on main; a dispatch
    from any other branch must not get a signature at all."""
    assert TRIGGERS["push"] == {"tags": ["v*"]}
    assert set(TRIGGERS["workflow_dispatch"]["inputs"]) == {"tag"}
    assert JOBS["charts"]["if"] == (
        "github.event_name == 'push' || github.ref == 'refs/heads/main'"
    )
    check, checkout = JOBS["charts"]["steps"][:2]
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in check["run"]
    assert checkout["with"]["ref"] == "refs/tags/${{ env.TAG }}"


def test_no_run_block_interpolates_an_input_or_a_step_output() -> None:
    for name, job in JOBS.items():
        for step in job["steps"]:
            run = step.get("run", "")
            assert not re.search(r"\$\{\{\s*(inputs|steps)\.", run), (name, step)


def test_the_charts_are_packaged_by_the_helm_ci_lints_with() -> None:
    helm = re.search(
        r'helmImage\s*=\s*"([^"]+)"', (REPO / ".dagger" / "main.go").read_text()
    )
    assert helm is not None
    assert JOBS["charts"]["env"]["HELM_IMAGE"] == helm.group(1)


def test_every_placeholder_in_the_notes_is_filled() -> None:
    """A placeholder the Notes step does not replace reaches the release page
    as `@NAME@`."""
    notes = (GITHUB / "release-notes.md").read_text()
    filled = JOBS["release"]["steps"][_runs(JOBS["release"]["steps"], "sed -e")]["run"]
    for name in set(re.findall(r"@([A-Z_]+)@", notes)):
        assert f"s|@{name}@|" in filled, name
