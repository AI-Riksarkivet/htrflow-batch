"""The Makefile's cluster targets fail closed (finding 3102).

`make e2e`, the GPU guard in `make install-devstack` and `make psa-labels`
each read the cluster through kubectl or helm, and each once took a failed
read for an answer: e2e reported success with no Job, a Failed Job or no
kubectl at all; the GPU guard read "kubectl failed" as "no GPU pods" and let
helm delete the RuntimeClass under them; psa-labels wrote an empty
`enforce=` when the release was missing. These drive the real targets and
the scripts they call with stub `kubectl`/`helm` on PATH, so every branch
runs without a cluster.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
NAMESPACE = "htr-batch"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("bash", "jq", "make")),
    reason="needs bash, jq and make",
)


class Stubs:
    """A bin directory put in front of PATH. Each stub logs its argv, one
    line per call, to `calls` and then runs the body the test gave it."""

    def __init__(self, root: Path):
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "calls.log"
        self.log.touch()

    def stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(
            f'#!/usr/bin/env bash\necho "{name} $*" >> "{self.log}"\n{body}\n',
            encoding="utf-8",
        )
        path.chmod(0o755)

    def calls(self, name: str) -> list[str]:
        return [
            line
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.startswith(name + " ")
        ]

    def run(self, *argv: str, **env: str) -> subprocess.CompletedProcess[str]:
        environ = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            **env,
        }
        # A value from the caller's own shell must not decide a test.
        for key in ("FORCE", "PSA_ENFORCE", "NVIDIA_DEVICE_PLUGIN", "MAKEFLAGS"):
            if key not in env:
                environ.pop(key, None)
        return subprocess.run(
            argv, cwd=REPO, env=environ, capture_output=True, text=True
        )

    def make(self, target: str, *args: str, **env: str):
        return self.run(
            "make",
            "--no-print-directory",
            target,
            "HTR_RELEASE=htr",
            f"HTR_NAMESPACE={NAMESPACE}",
            *args,
            **env,
        )


@pytest.fixture
def stubs(tmp_path: Path) -> Stubs:
    return Stubs(tmp_path)


# --- make e2e: scripts/e2e-wait.sh ----------------------------------------

SELECTOR = "htrflow.riksarkivet.se/campaign"


def _kubectl_jobs(
    jobs: dict[str, str] | None,
    *,
    listed: tuple[str, ...] | None = None,
    warmup_rc: int = 0,
) -> str:
    """A kubectl that knows the warm-up wait, the Job list and each Job's
    `completions|completedIndexes|conditions` line. `jobs=None` makes the
    list itself fail, as an unreachable API server does; `listed` lists
    Jobs that then cannot be read."""
    names = listed if listed is not None else tuple(jobs or ())
    listing = (
        'echo "error: the server could not be reached" >&2; exit 1'
        if jobs is None
        else "printf '%s\\n' " + " ".join(f"job.batch/{j}" for j in names)
        if names
        else "exit 0"
    )
    cases = "\n".join(
        f'  job.batch/{name}) echo "{line}" ;;' for name, line in (jobs or {}).items()
    )
    return f"""
case "$*" in
  *" wait "*) exit {warmup_rc} ;;
  *"get job -l"*) {listing}; exit 0 ;;
esac
case "$4" in
{cases}
  *) echo "Error from server (NotFound)" >&2; exit 1 ;;
esac
"""


def _e2e_wait(stubs: Stubs, timeout: str = "30"):
    return stubs.run(
        "bash",
        "scripts/e2e-wait.sh",
        NAMESPACE,
        SELECTOR,
        timeout,
        E2E_POLL_SECONDS="0",
    )


def test_e2e_passes_when_every_campaign_job_completes(stubs: Stubs):
    stubs.stub(
        "kubectl",
        _kubectl_jobs({"a": "3|0-2|SuccessCriteriaMet Complete", "b": "1|0|Complete"}),
    )
    result = _e2e_wait(stubs)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "job.batch/a completions=3 completedIndexes=[0-2]" in result.stdout


def test_e2e_fails_on_a_failed_job(stubs: Stubs):
    """A Failed Job is a terminal condition, and the loop used to stop
    waiting on it -- and then exit 0, as if it had completed."""
    stubs.stub(
        "kubectl",
        _kubectl_jobs({"a": "3|0-2|Complete", "b": "2|0|FailureTarget Failed"}),
    )
    result = _e2e_wait(stubs)
    assert result.returncode != 0
    assert "failed: job.batch/b" in result.stdout + result.stderr


def test_e2e_fails_when_no_campaign_job_exists(stubs: Stubs):
    """No Jobs means nothing ran -- a render that produced nothing, or the
    wrong namespace -- not that everything finished."""
    stubs.stub("kubectl", _kubectl_jobs({}))
    result = _e2e_wait(stubs)
    assert result.returncode != 0
    assert "no campaign Job" in result.stdout + result.stderr


def test_e2e_fails_when_the_job_list_cannot_be_read(stubs: Stubs):
    stubs.stub("kubectl", _kubectl_jobs(None))
    result = _e2e_wait(stubs)
    assert result.returncode != 0
    assert "the server could not be reached" in result.stderr


def test_e2e_fails_when_a_job_cannot_be_read(stubs: Stubs):
    """A Job that vanished (or a kubectl that failed) between the list and
    the read printed an empty line and counted as still pending, or --
    with a condition from the previous Job -- as done."""
    stubs.stub("kubectl", _kubectl_jobs({"a": "3|0-2|Complete"}, listed=("gone", "a")))
    result = _e2e_wait(stubs)
    assert result.returncode != 0
    assert "NotFound" in result.stderr


def test_e2e_fails_when_the_warm_up_does_not_complete(stubs: Stubs):
    stubs.stub("kubectl", _kubectl_jobs({"a": "1|0|Complete"}, warmup_rc=1))
    result = _e2e_wait(stubs)
    assert result.returncode != 0
    # One call -- the wait -- and it was for the warm-up; nothing after it.
    [call] = stubs.calls("kubectl")
    assert " wait " in call and f"{SELECTOR},app=htrflow-warmup" in call


@pytest.mark.parametrize(
    "conditions",
    ["SuccessCriteriaMet", "FailureTarget"],
    ids=["success-criteria", "failure-target"],
)
def test_an_interim_condition_is_not_the_end(stubs: Stubs, conditions: str):
    """Kubernetes sets SuccessCriteriaMet or FailureTarget before Complete or
    Failed, while pods still run: counted as done, the wait would stop early
    (test audit TA-infra-17). Only the deadline ends it."""
    stubs.stub("kubectl", _kubectl_jobs({"a": f"3|0-2|{conditions}"}))
    result = _e2e_wait(stubs, timeout="0")
    assert result.returncode != 0
    assert "still running: job.batch/a" in result.stdout + result.stderr


def test_e2e_gives_up_at_the_deadline(stubs: Stubs):
    stubs.stub("kubectl", _kubectl_jobs({"a": "3|0|"}))
    result = _e2e_wait(stubs, timeout="0")
    assert result.returncode != 0
    assert "still running: job.batch/a" in result.stdout + result.stderr


def test_make_e2e_waits_through_the_script():
    """The target is the script plus what precedes it; a copy of the loop in
    the Makefile would be the untested one."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\ne2e:\n", 1)[1].split("\n\n", 1)[0]
    assert "scripts/e2e-wait.sh $(HTR_NAMESPACE)" in recipe
    assert "while" not in recipe


# --- make install-devstack NVIDIA_DEVICE_PLUGIN=false ---------------------

GPU_POD = {
    "metadata": {"namespace": "htr-batch", "name": "campaign-0"},
    "spec": {"containers": [{"resources": {"limits": {"nvidia.com/gpu": 1}}}]},
    "status": {"phase": "Running"},
}
CPU_POD = {
    "metadata": {"namespace": "htr-batch", "name": "web"},
    "spec": {"containers": [{"resources": {}}]},
    "status": {"phase": "Running"},
}


def _pods(*items: dict) -> str:
    return "cat <<'JSON'\n" + json.dumps({"items": list(items)}) + "\nJSON"


def _install_devstack(stubs: Stubs, *args: str, **env: str):
    stubs.stub("helm", "exit 0")
    return stubs.make(
        "install-devstack", "NVIDIA_DEVICE_PLUGIN=false", "KYVERNO=false", *args, **env
    )


def test_the_plugin_goes_when_no_gpu_pod_needs_it(stubs: Stubs):
    stubs.stub("kubectl", _pods(CPU_POD))
    result = _install_devstack(stubs)
    assert result.returncode == 0, result.stdout + result.stderr
    [helm] = stubs.calls("helm")
    assert "--set nvidiaDevicePlugin.enabled=false" in helm


def test_the_plugin_stays_while_a_gpu_pod_runs(stubs: Stubs):
    stubs.stub("kubectl", _pods(CPU_POD, GPU_POD))
    result = _install_devstack(stubs)
    assert result.returncode != 0
    assert "GPU pods are running" in result.stdout + result.stderr
    assert "htr-batch/campaign-0" in result.stdout + result.stderr
    assert stubs.calls("helm") == []


RUNTIME_ONLY_POD = {
    "metadata": {"namespace": "htr-batch", "name": "warm-gpu"},
    "spec": {"runtimeClassName": "nvidia", "containers": [{"resources": {}}]},
    "status": {"phase": "Running"},
}
PENDING_GPU_POD = {
    **GPU_POD,
    "metadata": {"namespace": "htr-batch", "name": "queued-0"},
}
PENDING_GPU_POD["status"] = {"phase": "Pending"}
SYSTEM_GPU_POD = {
    **GPU_POD,
    "metadata": {"namespace": "kube-system", "name": "nvidia-device-plugin-x"},
}
FINISHED_GPU_POD = {**GPU_POD, "metadata": {"namespace": "htr-batch", "name": "done-0"}}
FINISHED_GPU_POD["status"] = {"phase": "Succeeded"}


@pytest.mark.parametrize(
    "pod",
    [RUNTIME_ONLY_POD, PENDING_GPU_POD],
    ids=["nvidia-runtime-no-request", "pending"],
)
def test_the_plugin_stays_for_every_pod_that_needs_it(stubs: Stubs, pod: dict):
    """A pod on the nvidia RuntimeClass needs it with no GPU request of its
    own, and a Pending pod is about to start on it (test audit TA-infra-16)."""
    stubs.stub("kubectl", _pods(CPU_POD, pod))
    result = _install_devstack(stubs)
    assert result.returncode != 0
    assert pod["metadata"]["name"] in result.stdout + result.stderr
    assert stubs.calls("helm") == []


@pytest.mark.parametrize(
    "pod", [SYSTEM_GPU_POD, FINISHED_GPU_POD], ids=["kube-system", "finished"]
)
def test_the_plugin_goes_past_pods_that_do_not_need_it(stubs: Stubs, pod: dict):
    """kube-system's own GPU pods (the device plugin itself) go with the
    chart, and a finished pod needs nothing."""
    stubs.stub("kubectl", _pods(CPU_POD, pod))
    result = _install_devstack(stubs)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_failed_pod_list_is_not_an_empty_one(stubs: Stubs):
    """The guard piped kubectl into jq with no pipefail: a kubectl that
    could not reach the cluster produced no pods, and helm went on to
    delete the RuntimeClass and DaemonSet the GPU pods run on."""
    stubs.stub("kubectl", 'echo "Unable to connect to the server" >&2; exit 1')
    result = _install_devstack(stubs)
    assert result.returncode != 0
    assert "cannot list the cluster's pods" in result.stdout + result.stderr
    assert stubs.calls("helm") == []


@pytest.mark.parametrize("answer", ["echo 'not json'", "exit 0", "echo '{}'"])
def test_an_unreadable_pod_list_is_not_an_empty_one(stubs: Stubs, answer: str):
    stubs.stub("kubectl", answer)
    result = _install_devstack(stubs)
    assert result.returncode != 0
    assert stubs.calls("helm") == []


def test_force_skips_the_check(stubs: Stubs):
    stubs.stub("kubectl", 'echo "must not be asked" >&2; exit 1')
    result = _install_devstack(stubs, "FORCE=1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert stubs.calls("kubectl") == []
    assert len(stubs.calls("helm")) == 1


# --- make psa-labels ------------------------------------------------------


def _helm_values(body: str) -> str:
    return f'case "$1 $2" in "get values") {body} ;; *) exit 99 ;; esac'


def test_the_enforce_level_comes_from_the_release(stubs: Stubs):
    stubs.stub(
        "helm", _helm_values('echo \'{"security": {"psaEnforce": "restricted"}}\'')
    )
    stubs.stub("kubectl", "exit 0")
    result = stubs.make("psa-labels")
    assert result.returncode == 0, result.stdout + result.stderr
    [label] = stubs.calls("kubectl")
    assert "pod-security.kubernetes.io/enforce=restricted" in label.split()
    assert "pod-security.kubernetes.io/warn=restricted" in label.split()


def test_no_release_yet_is_the_chart_default(stubs: Stubs):
    """Labelling before the first install is the documented order for an
    override; without one it is the chart's own default, said out loud --
    not the empty `enforce=` it used to write."""
    stubs.stub("helm", _helm_values('echo "Error: release: not found" >&2; exit 1'))
    stubs.stub("kubectl", "exit 0")
    result = stubs.make("psa-labels")
    assert result.returncode == 0, result.stdout + result.stderr
    [label] = stubs.calls("kubectl")
    assert "pod-security.kubernetes.io/enforce=baseline" in label.split()
    assert "no release htr" in result.stdout


def test_an_unreadable_release_is_refused(stubs: Stubs):
    """Any other helm failure says nothing about the level: labelling
    `baseline` then would quietly downgrade a `restricted` namespace."""
    stubs.stub(
        "helm", _helm_values('echo "Error: Kubernetes cluster unreachable" >&2; exit 1')
    )
    stubs.stub("kubectl", "exit 0")
    result = stubs.make("psa-labels")
    assert result.returncode != 0
    assert "Kubernetes cluster unreachable" in result.stderr
    assert stubs.calls("kubectl") == []


@pytest.mark.parametrize("level", ["", "privileged", "restricted "])
def test_a_level_the_chart_does_not_take_is_refused(stubs: Stubs, level: str):
    stubs.stub(
        "helm",
        _helm_values(
            "echo " + json.dumps(json.dumps({"security": {"psaEnforce": level}}))
        ),
    )
    stubs.stub("kubectl", "exit 0")
    result = stubs.make("psa-labels")
    assert result.returncode != 0
    assert "baseline or restricted" in result.stdout + result.stderr
    assert stubs.calls("kubectl") == []


def test_the_override_wins_and_is_checked(stubs: Stubs):
    stubs.stub("helm", 'echo "must not be asked" >&2; exit 1')
    stubs.stub("kubectl", "exit 0")
    result = stubs.make("psa-labels", "PSA_ENFORCE=restricted")
    assert result.returncode == 0, result.stdout + result.stderr
    assert stubs.calls("helm") == []
    assert "pod-security.kubernetes.io/enforce=restricted" in (
        stubs.calls("kubectl")[0].split()
    )

    refused = stubs.make("psa-labels", "PSA_ENFORCE=privileged")
    assert refused.returncode != 0
    assert len(stubs.calls("kubectl")) == 1
