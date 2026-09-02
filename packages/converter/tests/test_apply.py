"""``htrflow-campaigns apply`` against a fake ``kubectl``.

The real command is the only thing between a campaigns repo and the cluster,
so what is asserted here is the *exact* command sequence: pipelines before
campaigns (a campaign Job mounts its pipeline's ConfigMap), ``--prune`` only
when asked and always scoped to the renderer's own label, and the Kueue
pause sync patching a Workload only when its ``spec.active`` disagrees with
what git says.

The stub is a real executable on ``PATH`` rather than a monkeypatched
``subprocess.run``: it is what proves the argv we build is the argv a shell
would see.
"""

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from htrflow_converter import render
from htrflow_converter.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"

_STUB = '''#!/usr/bin/env python3
"""Fake kubectl: log every argv, answer reads from a canned map."""
import json, os, sys

args = sys.argv[1:]
with open(os.environ["KUBECTL_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
canned = json.load(open(os.environ["KUBECTL_CANNED"]))

if "apply" in args:
    print("applied")
    sys.exit(canned.get("apply_rc", 0))
if "patch" in args:
    print("patched")
    sys.exit(0)
if "get" in args:
    rest = args[args.index("get") + 1 :]
    kind = rest[0]
    if kind == "job":
        uid = canned.get("uids", {}).get(rest[1], "")
        if not uid:
            print("Error from server (NotFound)", file=sys.stderr)
            sys.exit(1)
        print(uid)
        sys.exit(0)
    if kind == "workload":  # list by label selector: empty is not an error
        uid = args[args.index("-l") + 1].split("=")[-1]
        print(canned.get("workloads", {}).get(uid, ""))
        sys.exit(0)
    print(canned.get("active", {}).get(kind, ""))  # get <workload/name>
    sys.exit(0)
print("unexpected argv: " + json.dumps(args), file=sys.stderr)
sys.exit(2)
'''


@pytest.fixture
def kubectl(tmp_path, monkeypatch):
    """A ``kubectl`` on PATH that logs argv and answers from ``canned``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "kubectl"
    stub.write_text(_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "kubectl.log"
    canned = tmp_path / "canned.json"
    canned.write_text("{}")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("KUBECTL_LOG", str(log))
    monkeypatch.setenv("KUBECTL_CANNED", str(canned))
    monkeypatch.setattr(time, "sleep", lambda _: None)

    class Kubectl:
        path = str(stub)

        def canned(self, **kwargs):
            canned.write_text(json.dumps(kwargs))

        def calls(self):
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines()]

        def verbs(self, verb):
            return [c for c in self.calls() if verb in c]

    return Kubectl()


def _repo(tmp_path, *, paused: str | None = None) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    if paused:
        (repo / "campaigns" / f"{paused}.yaml").write_text(
            "pipeline: demo-v1\nsuspend: true\nvolumes:\n  - R7777777\n"
        )
    return repo


def test_apply_applies_pipelines_before_campaigns(tmp_path, kubectl):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert main(["apply", str(repo), "--out", str(out)]) == 0
    assert kubectl.verbs("apply") == [
        ["apply", "-f", str(out / "pipelines")],
        ["apply", "-f", str(out / "campaigns")],
    ]


def test_apply_without_out_renders_into_a_temp_dir(tmp_path, kubectl):
    repo = _repo(tmp_path)
    assert main(["apply", str(repo)]) == 0
    applies = kubectl.verbs("apply")
    assert len(applies) == 2
    rendered = Path(applies[0][-1]).parent
    assert not rendered.exists(), "the temp render outlived the command"
    assert not rendered.is_relative_to(repo)


def test_prune_passes_the_renderers_selector_and_both_directories(tmp_path, kubectl):
    """``--prune`` deletes every object carrying CAMPAIGN_SELECTOR that is
    not in *this* apply — so the pruning apply has to see the pipelines too,
    or it would delete the pipeline ConfigMaps and warm-up Jobs it just
    applied."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert main(["apply", str(repo), "--out", str(out), "--prune"]) == 0
    assert kubectl.verbs("apply") == [
        ["apply", "-f", str(out / "pipelines")],
        [
            "apply",
            "--prune",
            "-l",
            render.CAMPAIGN_SELECTOR,
            "-f",
            str(out / "pipelines"),
            "-f",
            str(out / "campaigns"),
        ],
    ]


def test_a_failed_apply_stops_the_command(tmp_path, kubectl):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    kubectl.canned(apply_rc=1)
    assert main(["apply", str(repo), "--out", str(out)]) == 1
    assert len(kubectl.verbs("apply")) == 1, "campaigns applied after a failure"


def test_the_workload_is_patched_only_when_active_differs(tmp_path, kubectl):
    """Git is the truth about what should run: a paused campaign's Workload
    is deactivated, a running one's is re-activated (Kueue deactivates a
    Workload on its own for a requeue limit). Both are no-ops when the
    cluster already agrees, so an apply of an unchanged repo is silent."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    kubectl.canned(
        uids={"kyrk": "uid-kyrk", "loc": "uid-loc", "pausy": "uid-pausy"},
        workloads={
            "uid-kyrk": "workload.kueue.x-k8s.io/job-kyrk-a",
            "uid-loc": "workload.kueue.x-k8s.io/job-loc-b",
            "uid-pausy": "workload.kueue.x-k8s.io/job-pausy-c",
        },
        active={
            "workload.kueue.x-k8s.io/job-kyrk-a": "true",  # agrees: no patch
            "workload.kueue.x-k8s.io/job-loc-b": "false",  # Kueue deactivated
            "workload.kueue.x-k8s.io/job-pausy-c": "true",  # paused in git
        },
    )
    assert main(["apply", str(repo), "--out", str(out)]) == 0
    assert kubectl.verbs("patch") == [
        [
            "-n",
            "htr-test",
            "patch",
            "workload.kueue.x-k8s.io/job-loc-b",
            "--type",
            "merge",
            "-p",
            '{"spec": {"active": true}}',
        ],
        [
            "-n",
            "htr-test",
            "patch",
            "workload.kueue.x-k8s.io/job-pausy-c",
            "--type",
            "merge",
            "-p",
            '{"spec": {"active": false}}',
        ],
    ]


def test_a_paused_campaign_whose_workload_never_appears_waits_then_fails(
    tmp_path, kubectl, capsys
):
    """The window in which a brand-new paused campaign has no Workload yet is
    exactly the window in which Kueue admits and starts it. Skipping it would
    run a campaign git says is paused, so the apply waits and then fails."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    kubectl.canned(uids={"kyrk": "uid-kyrk", "loc": "uid-loc", "pausy": "uid-p"})
    rc = main(["apply", str(repo), "--out", str(out), "--pause-wait", "3"])
    assert rc == 1
    lookup = ["-n", "htr-test", "get", "workload", "-l",
              "kueue.x-k8s.io/job-uid=uid-p", "-o", "name"]  # fmt: skip
    lookups = [c for c in kubectl.verbs("workload") if "-l" in c]
    assert lookups.count(lookup) == 4, lookups  # once, then --pause-wait retries
    assert "pausy: paused in git" in capsys.readouterr().err


def test_a_running_campaign_without_a_workload_is_skipped(tmp_path, kubectl):
    """A Workload that does not exist is not admitted either — the next
    apply catches it. Only a *paused* campaign has to wait."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    kubectl.canned(uids={"kyrk": "uid-kyrk", "loc": "uid-loc"})
    assert main(["apply", str(repo), "--out", str(out), "--pause-wait", "3"]) == 0
    assert kubectl.verbs("patch") == []
    lookups = [c for c in kubectl.verbs("workload") if "-l" in c]
    assert len(lookups) == 2, lookups  # one each, no retries


def test_dry_run_renders_but_runs_nothing(tmp_path, kubectl, capsys):
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    assert main(["apply", str(repo), "--out", str(out), "--dry-run"]) == 0
    assert kubectl.calls() == []
    assert (out / "campaigns" / "pausy.yaml").exists()
    err = capsys.readouterr().err
    assert f"kubectl apply -f {out / 'pipelines'}" in err


def test_a_render_error_never_reaches_the_cluster(tmp_path, kubectl, capsys):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    (repo / "pipelines" / "demo-v1.yaml").write_text("image: nodigest\nsteps: []\n")
    assert main(["apply", str(repo), "--out", str(out)]) == 1
    assert kubectl.calls() == []
    assert "digest" in capsys.readouterr().out


def test_kubectl_binary_is_configurable(tmp_path, kubectl):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert main(["apply", str(repo), "--out", str(out), "--kubectl", kubectl.path]) == 0
    assert len(kubectl.verbs("apply")) == 2
