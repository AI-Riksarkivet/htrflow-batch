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


PUBLISH = yaml.safe_load((WORKFLOWS / "publish.yml").read_text())
JOBS = PUBLISH["jobs"]
IDENTITY = (
    r"^https://github\.com/AI-Riksarkivet/htrflow-batch/"
    r"\.github/workflows/publish\.yml@refs/heads/main$"
)


def _script(job: str) -> str:
    return "\n".join(step.get("run", "") for step in JOBS[job]["steps"])


def test_publish_runs_on_main_and_by_hand_only():
    # PyYAML reads the bare key `on` as the boolean True.
    on = PUBLISH[True]
    assert set(on) == {"push", "workflow_dispatch"}
    assert on["push"] == {"branches": ["main"]}
    # recovery re-runs the release for the version on main: nothing to type
    assert not (on["workflow_dispatch"] or {}).get("inputs")
    assert PUBLISH["permissions"] == {"contents": "read"}


def test_one_release_at_a_time():
    assert PUBLISH["concurrency"] == {"group": "release", "cancel-in-progress": False}


def test_every_release_job_waits_for_the_plan_to_say_publish():
    plan = JOBS["plan"]
    assert "environment" not in plan and "secrets." not in yaml.safe_dump(plan)
    assert set(plan["outputs"]) == {"state", "version"}
    checkout = plan["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0  # `state` reads the tags
    assert "python3 scripts/release.py state" in _script("plan")


def test_a_push_queued_behind_a_release_does_not_release_it_again():
    """A push to main during a release waits in the concurrency group and
    runs after the pin PR merged, from a commit without the pins: its own
    files say `publish` for a version that is done. The plan asks main's tip
    too, and lets it overrule a `publish` for the same version."""
    script = _script("plan")
    assert "git fetch --no-tags origin main" in script
    assert 'python3 "$tip/scripts/release.py" state' in script
    for name, job in JOBS.items():
        if name == "plan":
            continue
        assert "plan" in job["needs"], name
        assert job["if"] == "needs.plan.outputs.state == 'publish'", name


def test_the_suite_runs_once_and_the_manifest_lists_wait_for_it():
    tests = JOBS["tests"]
    assert "environment" not in tests and "DOCKERHUB_" not in yaml.safe_dump(tests)
    script = _script("tests")
    assert "dagger call --progress plain checks" in script
    assert "dagger call --progress plain test" in script
    assert "tests" in JOBS["manifest"]["needs"]
    publish = _script("publish")
    assert "publish-docker" in publish and "--skip-tests" in publish
    assert "dagger call --progress plain test" not in publish


def test_a_reused_per_arch_tag_must_verify():
    steps = JOBS["publish"]["steps"]
    runs = [s.get("run", "") for s in steps]
    verify = next(i for i, r in enumerate(runs) if "cosign verify" in r)
    dagger = next(i for i, r in enumerate(runs) if "publish-docker" in r)
    assert verify < dagger
    step = steps[verify]
    env = {**JOBS["publish"]["env"], **step.get("env", {})}
    assert env["IDENTITY"] == IDENTITY
    assert env["ISSUER"] == "https://token.actions.githubusercontent.com"
    assert '--certificate-identity-regexp "$IDENTITY"' in step["run"]
    assert '--certificate-oidc-issuer "$ISSUER"' in step["run"]
    # the bare tag existing is the manifest list: the release already happened
    assert '"${IMAGE}:${TAG}"' in step["run"]
    # a verified tag skips the build and the second signature
    reused = "steps.existing.outputs.digest == ''"
    assert steps[dagger]["if"] == reused
    signing = next(s for s in steps if s.get("uses") == "./.github/actions/sign-attest")
    assert signing["if"] == reused
    uses = [s.get("uses", "") for s in steps]
    installer = next(i for i, u in enumerate(uses) if "cosign-installer" in u)
    login = next(i for i, u in enumerate(uses) if u.startswith("docker/login-action"))
    assert installer < login < verify  # tools before the credential


def test_the_tag_is_the_version_on_the_pushed_commit():
    for name in ("publish", "manifest"):
        assert JOBS[name]["env"]["TAG"] == "v${{ needs.plan.outputs.version }}"


def test_the_manifest_job_hands_its_digest_to_the_pin_job():
    steps = JOBS["manifest"]["steps"]
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    assert upload["with"]["name"] == "manifest-${{ matrix.component }}"
    download = next(
        s for s in JOBS["pin"]["steps"] if "download-artifact" in s.get("uses", "")
    )
    assert download["with"]["pattern"] == "manifest-*"


def test_the_pin_job_holds_no_registry_credential_and_starts_the_required_checks():
    pin = JOBS["pin"]
    assert set(pin["needs"]) == {"plan", "manifest"}
    assert "environment" not in pin
    assert "DOCKERHUB_" not in yaml.safe_dump(pin)
    assert pin["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "actions": "write",
    }
    assert pin["env"]["BRANCH"] == "release/v${{ needs.plan.outputs.version }}-pin"
    script = _script("pin")
    assert "python3 scripts/release.py pin" in script
    assert '--converter-ref "$GITHUB_SHA"' in script
    assert "release: pin the v${VERSION} images and converter" in script
    assert "41898283+github-actions[bot]@users.noreply.github.com" in script
    dispatches = [
        'gh workflow run ci.yml --ref "$BRANCH" -f pull_request_checks=true',
        'gh workflow run codeql.yml --ref "$BRANCH"',
        'gh workflow run trufflehog.yml --ref "$BRANCH"',
        "gh pr merge",
    ]
    at = [script.index(d) for d in dispatches]
    assert at == sorted(at), "auto-merge is enabled after the checks start"
    assert "--auto --merge" in script


def test_the_pin_job_starts_main_checks_after_the_merge():
    script = _script("pin")
    merge = script.index("--auto --merge")
    poll = script.index("gh pr view", merge)
    assert "MERGED" in script[poll:]
    assert "sleep 30" in script[poll:]
    published = script.index("gh workflow run published.yml --ref main", poll)
    ci = script.index("gh workflow run ci.yml --ref main", poll)
    assert poll < published and poll < ci


def test_the_pin_job_token_never_meets_the_workspace_code():
    """`pin` runs the workspace's own programs (uv sync, htrflow-campaigns
    init, the config reference). The token that can push and dispatch is
    given only to the steps that call gh or push, never left in the
    checkout or the job's environment."""
    pin = JOBS["pin"]
    assert "GH_TOKEN" not in pin["env"]
    workspace = 0
    for step in pin["steps"]:
        run, env = step.get("run", ""), step.get("env", {})
        workspace += "uv sync" in run or "python3 scripts/release.py" in run
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
        if "uv sync" in run or "python3 scripts/release.py" in run:
            assert "GH_TOKEN" not in env, step.get("name")
            assert "github.token" not in yaml.safe_dump(step), step.get("name")
        if "gh " in run or "git push" in run:
            assert env.get("GH_TOKEN") == "${{ github.token }}", step.get("name")
    assert workspace == 2  # the needles above still find both steps
