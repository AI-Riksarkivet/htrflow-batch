import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from htrflow_converter import render
from htrflow_converter.models import Campaign, ConverterConfig, Toleration, Volume
from htrflow_converter.parse import load

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
GOLDEN = Path(__file__).parent / "golden"


def _good():
    return load(GOOD / "campaigns", GOOD / "pipelines", GOOD / "converter.yaml")


def _kyrk():
    campaigns, pipelines, cfg = _good()
    kyrk = next(c for c in campaigns if c.name == "kyrk")
    return kyrk, pipelines["demo-v1"], cfg


def test_pipeline_objects_match_golden():
    _, demo, cfg = _kyrk()
    objs = render.pipeline_objects(demo, cfg)
    golden = list(yaml.safe_load_all((GOLDEN / "demo-v1.pipeline.yaml").read_text()))
    assert objs == golden


def test_campaign_configmap_matches_golden():
    kyrk, demo, cfg = _kyrk()
    cm = render.campaign_objects(kyrk, demo, cfg)[0]
    assert cm == yaml.safe_load((GOLDEN / "kyrk.configmap.yaml").read_text())


def test_campaign_job_matches_golden():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    assert job == yaml.safe_load((GOLDEN / "kyrk.job.yaml").read_text())


def test_campaign_job_fields_per_global_constraints():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    spec = job["spec"]
    assert spec["completionMode"] == "Indexed"
    assert spec["completions"] == 3
    assert spec["parallelism"] == cfg.window
    assert spec["backoffLimitPerIndex"] == 3
    assert spec["maxFailedIndexes"] == 3
    assert spec["ttlSecondsAfterFinished"] == cfg.ttl_seconds_after_finished
    assert spec["template"]["spec"]["restartPolicy"] == "Never"
    # The wrapper's SIGTERM cleanup (log-ship join + final bounded S3 PUT)
    # does not fit in the default 30 s.
    assert spec["template"]["spec"]["terminationGracePeriodSeconds"] == 120
    assert "suspend" not in spec
    rules = spec["podFailurePolicy"]["rules"]
    assert rules[0] == {
        "action": "Ignore",
        "onPodConditions": [{"type": "DisruptionTarget"}],
    }
    assert rules[1]["action"] == "FailIndex"
    assert rules[1]["onExitCodes"] == {
        "containerName": "wrapper",
        "operator": "In",
        "values": [13],
    }
    # Only Argo CD's Skip (3085): no partial-admission annotation (B63).
    assert job["metadata"]["annotations"] == {"argocd.argoproj.io/hook": "Skip"}
    labels = job["metadata"]["labels"]
    assert labels["app"] == "htrflow-batch"
    assert labels["kueue.x-k8s.io/queue-name"] == cfg.queue
    assert labels["htrflow.riksarkivet.se/campaign"] == "kyrk"
    assert labels["htrflow.riksarkivet.se/pipeline"] == "demo-v1"
    assert labels["htrflow.riksarkivet.se/managed-by"] == "converter"


def test_no_volume_ref_or_iiif_manifest_url_env_set_by_python():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    container = job["spec"]["template"]["spec"]["containers"][0]
    names = {e["name"] for e in container["env"]}
    assert "VOLUME_REF" not in names
    assert "IIIF_MANIFEST_URL" not in names


def test_shell_args_contain_a_real_tab_and_exec():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["/bin/sh", "-c"]
    args = container["args"][0]
    assert "\t" in args
    assert "exec python -m htrflow_batch" in args


def test_both_jobs_make_the_writable_dirs_before_exec():
    """readOnlyRootFilesystem: HOME/TMPDIR/YOLO_CONFIG_DIR point into the
    tmpfs workdir and must exist before htrflow builds a model. The shell
    prologue does it -- the wrapper no longer mkdirs them itself (Task 25
    item 2). The warm-up also makes HF_HOME: a fresh cache PVC has no
    /data/hf."""
    kyrk, demo, cfg = _kyrk()
    campaign = render.campaign_objects(kyrk, demo, cfg)[1]
    args = campaign["spec"]["template"]["spec"]["containers"][0]["args"][0]
    mkdir = 'mkdir -p "$HOME" "$TMPDIR" "$YOLO_CONFIG_DIR"'
    assert mkdir in args
    assert args.index(mkdir) < args.index("exec python")

    warmup = render.pipeline_objects(demo, cfg)[1]
    container = warmup["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["/bin/sh", "-c"]
    warm_args = container["args"][0]
    assert mkdir + ' "$HF_HOME"' in warm_args
    assert "exec python -m htrflow_batch.warmup" in warm_args


def test_init_container_present_with_pipeline_marker_path():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    init = job["spec"]["template"]["spec"]["initContainers"]
    assert len(init) == 1
    assert init[0]["name"] == "warmup-wait"
    assert init[0]["image"] == demo.image
    assert f"/data/warmup/{demo.id}.done" in init[0]["command"][-1]
    assert "nvidia.com/gpu" not in init[0]["resources"]["requests"]


def _run_gate(tmp_path, pod: dict, marker: str, after: int | None = None):
    """Run a pod's rendered ``warmup-wait`` command under its own shell,
    with the marker at a path under ``tmp_path`` and ``sleep`` a shell
    function that logs its seconds instead of waiting -- creating the marker
    once ``after`` sleeps are logged. -> (exit code, stderr, seconds slept,
    the marker's stand-in path)."""
    command = pod["initContainers"][0]["command"]
    here, log = tmp_path / "warmup.done", tmp_path / "slept"
    log.write_text("")
    here.unlink(missing_ok=True)
    if after == 0:
        here.touch()
    stub = (
        f'sleep() {{ echo "$1" >> {log}; '
        f'[ "$(wc -l < {log})" -lt {after or 10**9} ] || touch {here}; }}; '
    )
    done = subprocess.run(
        [*command[:-1], stub + command[-1].replace(marker, str(here))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    slept = sum(int(n) for n in log.read_text().split())
    return done.returncode, done.stderr, slept, str(here)


def test_the_warmup_wait_is_bounded_and_fails_the_index(tmp_path):
    """An unbounded `until [ -f … ]` holds `nvidia.com/gpu: 1` for the pod's
    whole deadline, once per retry, whenever a pipeline's warm-up never wrote
    its marker (audit X3). The init container now gives up after
    `warmup_wait_seconds`, says on stderr which marker it waited for, and
    exits 13 -- which the Job's podFailurePolicy turns into a failed index
    instead of three more six-hour waits. The rendered script is run, not
    read: a counter that steps too fast, or a test that exits on the first
    pass, reads the same as the right one."""
    kyrk, demo, cfg = _kyrk()
    cfg = cfg.model_copy(update={"warmup_wait_seconds": 120})
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    pod = job["spec"]["template"]["spec"]
    init = pod["initContainers"][0]
    marker = f"/data/warmup/{demo.id}.done"

    # Never there: the whole 120 s is waited -- not one step less, which a
    # `-lt` would give while printing "after 120s" -- and then exit 13,
    # naming the marker.
    rc, err, slept, here = _run_gate(tmp_path, pod, marker)
    assert (rc, slept) == (13, 120)
    assert here in err and "after 120s" in err
    # There from the start: through at once. Arriving late: through then.
    assert _run_gate(tmp_path, pod, marker, after=0)[:3] == (0, "", 0)
    assert _run_gate(tmp_path, pod, marker, after=3)[:3] == (0, "", 30)

    # The default policy (`File`) reads /dev/termination-log, which a shell
    # script never writes: without this the index fails with an EMPTY
    # termination message and the campaign card shows no reason at all.
    assert init["terminationMessagePolicy"] == "FallbackToLogsOnError"

    rules = job["spec"]["podFailurePolicy"]["rules"]
    # The order is semantics: Kubernetes takes the FIRST matching rule, so
    # the disruption `Ignore` stays ahead of every exit-code rule.
    assert rules[0]["action"] == "Ignore"
    assert rules[-1] == {
        "action": "FailIndex",
        "onExitCodes": {
            "containerName": "warmup-wait",
            "operator": "In",
            "values": [13],
        },
    }


def test_the_warmup_wait_never_outlasts_the_pods_own_deadline(tmp_path):
    """A pipeline whose `max_seconds:` is shorter than `warmup_wait_seconds`
    would have its pod killed by the kubelet -- exit 143, matched by no
    `FailIndex` rule -- before the gate could ever give up, so the index
    would be retried three times, holding a GPU through every wait. That is
    the failure the bound exists to stop, so the rendered wait stays STRICTLY
    below the deadline -- by one sleep step, since the two clocks do not
    start together (the pod's runs from pod start, the gate's from the init
    container's first line) and only the gate turns the failure into exit 13,
    a `FailIndex` and a sentence. A tie would hand it back to the kubelet."""
    _, demo, cfg = _kyrk()
    cfg = cfg.model_copy(update={"warmup_wait_seconds": 900})
    marker = f"/data/warmup/{demo.id}.done"
    c = Campaign(
        name="kyrk",
        pipeline="demo-v1",
        volumes=[Volume(id="v1", manifest="https://x/y")],
    )

    def gate(pipeline):
        """(the pod's deadline, seconds the gate waits before exit 13)."""
        pod = render.campaign_objects(c, pipeline, cfg)[1]["spec"]["template"]["spec"]
        rc, err, slept, _ = _run_gate(tmp_path, pod, marker)
        assert rc == 13 and f"after {slept}s" in err
        return pod["activeDeadlineSeconds"], slept

    deadline, waited = gate(demo.model_copy(update={"max_seconds": 600}))
    assert deadline == 600
    assert waited == deadline - render._WAIT_STEP, "gives up one step early"

    # A deadline under one step still has to render a runnable script: the
    # gate cannot win that race, but it must not give up before it starts.
    assert gate(demo.model_copy(update={"max_seconds": 5})) == (5, render._WAIT_STEP)

    # The other way round, the pipeline's deadline is none of the gate's
    # business: 900 s of waiting inside a 6 h budget is what it is for.
    assert gate(demo) == (21600, 900)


def test_the_warmup_deadline_is_the_pods_and_not_the_jobs():
    """A Job-level `activeDeadlineSeconds` makes the JOB controller delete the
    pod, and the termination message the warm-up writes on SIGTERM goes with
    it -- the campaign card's warm-up chip has nowhere else to read it from.
    On the pod template the kubelet does the killing instead: the pod is left
    behind with `status.reason: DeadlineExceeded`, exit 143 and its message
    intact, and the attempt is counted, so `backoffLimit: 2` still applies --
    a warm-up killed by a slow first download is retried rather than being
    terminally failed on the first try."""
    _, demo, cfg = _kyrk()
    job = render.pipeline_objects(demo, cfg)[1]
    assert "activeDeadlineSeconds" not in job["spec"]
    assert job["spec"]["template"]["spec"]["activeDeadlineSeconds"] == 3600
    assert job["spec"]["backoffLimit"] == 2


def test_the_warmup_job_schedules_where_the_campaign_job_does():
    """A warm-up Job without the campaign's `runtimeClassName`, `nodeSelector`
    and `tolerations` lands on whatever node will take it, fills a *different*
    ReadWriteOnce cache PV, and the marker never appears where the batch pods
    wait -- the same held GPU, on a correctly tainted cluster (audit X5)."""
    kyrk, demo, cfg = _kyrk()
    cfg = cfg.model_copy(
        update={
            "runtime_class": "nvidia",
            "node_selector": {"gpu": "true"},
            "tolerations": [
                Toleration(key="gpu", operator="Exists", effect="NoSchedule")
            ],
        }
    )
    warmup = render.pipeline_objects(demo, cfg)[1]["spec"]["template"]["spec"]
    campaign = render.campaign_objects(kyrk, demo, cfg)[1]["spec"]["template"]["spec"]

    fields = ("runtimeClassName", "nodeSelector", "tolerations")
    assert {f: warmup[f] for f in fields} == {f: campaign[f] for f in fields}
    assert warmup["runtimeClassName"] == "nvidia"


def test_campaign_volume_mounted_from_the_campaign_configmap():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    volumes = job["spec"]["template"]["spec"]["volumes"]
    campaign_vol = next(v for v in volumes if v["name"] == "campaign")
    assert campaign_vol["configMap"]["name"] == "campaign-kyrk"


def test_split_10001_volumes_makes_two_parts():
    volumes = [
        Volume(id=f"v{i}", manifest=f"https://example.org/{i}") for i in range(10001)
    ]
    parts = render.split(volumes)
    assert len(parts) == 2
    assert len(parts[0]) == 10_000
    assert len(parts[1]) == 1


def test_campaign_objects_for_10001_volumes_makes_two_jobs_and_configmaps():
    _, demo, cfg = _kyrk()
    volumes = [
        Volume(id=f"v{i}", manifest=f"https://example.org/{i}") for i in range(10001)
    ]
    c = Campaign(name="kyrk", pipeline="demo-v1", volumes=volumes)
    cm1, job1, cm2, job2 = render.campaign_objects(c, demo, cfg)
    assert job1["metadata"]["name"] == "kyrk-part1"
    assert job1["spec"]["completions"] == 10_000
    assert job2["metadata"]["name"] == "kyrk-part2"
    assert job2["spec"]["completions"] == 1
    assert cm1["metadata"]["name"] == "campaign-kyrk-part1"
    assert cm2["metadata"]["name"] == "campaign-kyrk-part2"


def test_s3_prefix_is_always_the_namespace():
    """The namespaced layout is the only layout (B63 Task 15): `S3_PREFIX` is
    the campaign's namespace, unconditionally. `converter.yaml` forbids extra
    keys, so a config still carrying the retired layout flag is rejected
    rather than silently ignored."""
    _, demo, cfg = _kyrk()
    v = [Volume(id="v1", manifest="https://x/y")]
    c = Campaign(name="kyrk", pipeline="demo-v1", volumes=v)
    job = render.campaign_objects(c, demo, cfg)[1]
    env = job["spec"]["template"]["spec"]["containers"][0]["env"]
    assert next(e["value"] for e in env if e["name"] == "S3_PREFIX") == "htr-test/"

    with pytest.raises(ValidationError):
        ConverterConfig(namespace="htr-test", retired_flag=True)


def test_priority_adds_the_kueue_priority_class_label():
    _, demo, cfg = _kyrk()
    v = [Volume(id="v1", manifest="https://x/y")]
    with_priority = Campaign(
        name="kyrk", pipeline="demo-v1", volumes=v, priority="high"
    )
    without_priority = Campaign(name="kyrk", pipeline="demo-v1", volumes=v)

    job = render.campaign_objects(with_priority, demo, cfg)[1]
    assert job["metadata"]["labels"]["kueue.x-k8s.io/priority-class"] == "high"

    job2 = render.campaign_objects(without_priority, demo, cfg)[1]
    assert "kueue.x-k8s.io/priority-class" not in job2["metadata"]["labels"]


def test_split_campaign_names_never_collide_even_when_close_to_63_chars():
    """A 58-character name plus `-part1` is 64, and the API server refuses it
    twice over: the Job controller copies `metadata.name` into the
    `batch.kubernetes.io/job-name` label VALUE (63 bytes), and an Indexed
    Job's pod is named `<job>-<index>`, which has to stay a 63-character DNS
    label ("will not able to create pod with invalid DNS label", measured
    against the PoC API server). So the name is cut at split time, and the
    parts stay distinct."""
    _, demo, cfg = _kyrk()
    name = "a" * 58
    volumes = [
        Volume(id=f"v{i}", manifest=f"https://example.org/{i}") for i in range(10_001)
    ]
    c = Campaign(name=name, pipeline="demo-v1", volumes=volumes)
    cm1, job1, cm2, job2 = render.campaign_objects(c, demo, cfg)

    stem = "a" * 50  # 63 - len("-part999") - len("-9999"), the reserved maxima
    assert job1["metadata"]["name"] != job2["metadata"]["name"]
    assert job1["metadata"]["name"] == f"{stem}-part1"
    assert job2["metadata"]["name"] == f"{stem}-part2"
    assert cm1["metadata"]["name"] == f"campaign-{stem}-part1"
    assert cm2["metadata"]["name"] == f"campaign-{stem}-part2"

    for cm, job in ((cm1, job1), (cm2, job2)):
        index = job["spec"]["completions"] - 1
        assert len(f"{job['metadata']['name']}-{index}") <= 63
        labels = {**job["metadata"]["labels"], **cm["metadata"]["labels"]}
        assert all(len(value) <= 63 for value in labels.values())
        # A ConfigMap name is a DNS-1123 SUBDOMAIN, not a label: 73 characters
        # is fine (the PoC API server accepts `campaign-<58 chars>-part1`).
        assert len(cm["metadata"]["name"]) <= 253

    def campaign_volume(job):
        volumes = job["spec"]["template"]["spec"]["volumes"]
        return next(v for v in volumes if v["name"] == "campaign")

    assert campaign_volume(job1)["configMap"]["name"] == cm1["metadata"]["name"]
    assert campaign_volume(job2)["configMap"]["name"] == cm2["metadata"]["name"]


def test_no_yaml_anchors_from_shared_security_context_objects():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    text = yaml.safe_dump_all([job], sort_keys=False)
    assert "&id0" not in text
    assert "*id0" not in text
    pod_spec = job["spec"]["template"]["spec"]
    wrapper_ctx = pod_spec["containers"][0]["securityContext"]
    init_ctx = pod_spec["initContainers"][0]["securityContext"]
    assert wrapper_ctx is not init_ctx


def test_node_selector_and_tolerations_appear_in_the_pod_spec():
    _, demo, cfg = _kyrk()
    cfg = cfg.model_copy(
        update={
            "node_selector": {"gpu": "true"},
            "tolerations": [
                Toleration(key="gpu", operator="Exists", effect="NoSchedule")
            ],
        }
    )
    v = [Volume(id="v1", manifest="https://x/y")]
    c = Campaign(name="kyrk", pipeline="demo-v1", volumes=v)
    job = render.campaign_objects(c, demo, cfg)[1]
    pod_spec = job["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"gpu": "true"}
    assert pod_spec["tolerations"] == [
        {"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}
    ]


@pytest.mark.skipif(
    shutil.which("kubeconform") is None, reason="kubeconform not on PATH"
)
def test_kubeconform_strict_passes_on_rendered_files(tmp_path):
    from htrflow_converter.cli import main

    out = tmp_path / "rendered"
    assert main(["render", str(GOOD), "--out", str(out)]) == 0
    files = sorted(str(p) for p in out.rglob("*.yaml"))
    env = dict(os.environ)
    env.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
    result = subprocess.run(
        ["kubeconform", "-strict", "-summary", *files],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


#: Pod Security `restricted`, as the chart's own `_helpers.tpl` spells it for
#: the pods it renders. `security.psaEnforce: restricted` (the production
#: profile) is a claim about every pod in the namespace, and most of them are
#: these -- rendered here, applied outside the chart, and so invisible to any
#: gate the chart has.
RESTRICTED_POD = {
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "runAsGroup": 1000,
    "fsGroup": 1000,
    "seccompProfile": {"type": "RuntimeDefault"},
}
RESTRICTED_CONTAINER = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}


def test_every_job_the_converter_renders_is_restricted_clean():
    """A campaign pod runs model code over images fetched from the network,
    for hours, on a GPU node, with the results bucket's write credentials.
    Both Jobs here are the ones the namespace's Pod Security level is really
    about, and nothing in the chart can check them: they are applied outside
    it. Every container, init containers included, and no API token in any
    of them."""
    kyrk, demo, cfg = _kyrk()
    rendered = [
        *render.pipeline_objects(demo, cfg),
        *render.campaign_objects(kyrk, demo, cfg),
    ]
    jobs = [o for o in rendered if o["kind"] == "Job"]
    assert {j["metadata"]["labels"]["app"] for j in jobs} == {
        "htrflow-warmup",
        "htrflow-batch",
    }
    for job in jobs:
        spec = job["spec"]["template"]["spec"]
        name = job["metadata"]["name"]
        assert spec["automountServiceAccountToken"] is False, name
        assert {
            k: spec["securityContext"].get(k) for k in RESTRICTED_POD
        } == RESTRICTED_POD, name
        containers = [*spec["containers"], *spec.get("initContainers", [])]
        assert containers, name
        for container in containers:
            assert {
                k: container["securityContext"].get(k) for k in RESTRICTED_CONTAINER
            } == RESTRICTED_CONTAINER, (name, container["name"])


def test_the_ci_test_image_carries_the_tools_this_suite_shells_out_to():
    """A `skipif` on a missing binary is a test that stops running without
    saying so, and two of them did exactly that for months: the CI pytest
    image had neither kubeconform (this file) nor git (test_apply.py), so the
    manifest validation and the commit provenance were never checked in CI
    while the suite reported green.

    The dagger test container installs git and copies kubeconform and helm
    out of the same digest-pinned images the chart render uses. This asserts
    it still does, because the skip cannot.
    """
    dagger = (Path(__file__).resolve().parents[3] / ".dagger" / "test.go").read_text()
    assert "--no-install-recommends git" in dagger
    assert '"/usr/local/bin/kubeconform"' in dagger
    assert '"/usr/local/bin/helm"' in dagger
    assert "m.withTestTools(container)." in dagger


def test_window_is_capped_by_the_converter_window():
    """`converter.yaml: window` is the per-cluster cap, not merely a default:
    a campaign may ask for less concurrency, never more. Rendering more than
    the queue can admit used to be handled by Kueue's partial admission, which
    rewrites `spec.parallelism` on the live Job and makes every later apply of
    the unchanged rendered file fail its own webhook."""
    kyrk, demo, cfg = _kyrk()
    assert cfg.window == 10
    over = kyrk.model_copy(update={"window": 40})
    assert render.campaign_objects(over, demo, cfg)[1]["spec"]["parallelism"] == 10
    under = kyrk.model_copy(update={"window": 2})
    assert render.campaign_objects(under, demo, cfg)[1]["spec"]["parallelism"] == 2
    unset = kyrk.model_copy(update={"window": None})
    assert render.campaign_objects(unset, demo, cfg)[1]["spec"]["parallelism"] == 10


def test_no_job_carries_the_partial_admission_annotation():
    kyrk, demo, cfg = _kyrk()
    for obj in render.pipeline_objects(demo, cfg) + render.campaign_objects(
        kyrk, demo, cfg
    ):
        assert "kueue.x-k8s.io/job-min-parallelism" not in str(obj["metadata"])


def test_suspend_true_renders_spec_suspend():
    """The campaign file's `suspend:` is the declared intent; the apply step
    (`htrflow-campaigns apply`) is what makes it stick under Kueue."""
    kyrk, demo, cfg = _kyrk()
    assert "suspend" not in render.campaign_objects(kyrk, demo, cfg)[1]["spec"]
    paused = kyrk.model_copy(update={"suspend": True})
    assert render.campaign_objects(paused, demo, cfg)[1]["spec"]["suspend"] is True


def test_pipeline_max_seconds_renders_the_pod_deadline():
    """The per-volume budget is the pod's own `activeDeadlineSeconds`, not a
    wrapper env var: the kubelet SIGTERMs at the deadline and the attempt is
    counted, so backoffLimitPerIndex retries the index (Task 25 item 1)."""
    kyrk, demo, cfg = _kyrk()

    def deadline(pipeline):
        job = render.campaign_objects(kyrk, pipeline, cfg)[1]
        pod = job["spec"]["template"]["spec"]
        env = pod["containers"][0]["env"]
        assert not [e for e in env if e["name"] == "MAX_SECONDS"]
        return pod["activeDeadlineSeconds"]

    assert deadline(demo) == cfg.max_seconds
    assert deadline(demo.model_copy(update={"max_seconds": 60})) == 60


@pytest.mark.parametrize(
    "name",
    [
        "campaign-job.yaml",
        "warmup-job.yaml",
        "configmap.yaml",
        "pipeline-configmap.yaml",
    ],
)
def test_skeletons_are_valid_jobs(name):
    """The packaged skeletons (render._load) are complete, well-formed
    objects on their own -- this is also what kubeconform validates as-is in
    CI (.dagger/checks.go)."""
    doc = render._load(name)
    assert doc["kind"] in ("Job", "ConfigMap")
    assert doc["apiVersion"] in ("batch/v1", "v1")
    if name == "campaign-job.yaml":
        assert doc["spec"]["completionMode"] == "Indexed"


def test_load_returns_a_fresh_copy_every_call():
    a = render._load("configmap.yaml")
    b = render._load("configmap.yaml")
    assert a == b
    assert a is not b
    a["metadata"]["name"] = "mutated"
    assert render._load("configmap.yaml")["metadata"]["name"] != "mutated"


def test_set_dotted_path():
    obj = {"a": {"b": [{"c": 1}, {"d": 2}]}}
    render._set(obj, "a.b[0].c", 99)
    assert obj["a"]["b"][0]["c"] == 99
    render._set(obj, "a.b[1].e", "new")  # a new leaf key may be created
    assert obj["a"]["b"][1]["e"] == "new"
    with pytest.raises(KeyError):
        render._set(obj, "a.nope.c", 1)  # unknown intermediate segment


def test_every_rendered_object_carries_the_prune_selector():
    """`htrflow-campaigns apply --prune` (and Argo CD's prune) is what makes
    "deleting a campaign file cancels the campaign" true. An object without
    the label survives its own deletion: the campaign ConfigMap did, and
    outlived the Job it fed. `cluster.py` lists by this same constant, so the
    label and the selector can never drift apart."""
    assert render.CAMPAIGN_SELECTOR == "htrflow.riksarkivet.se/managed-by=converter"
    key, _, value = render.CAMPAIGN_SELECTOR.partition("=")
    kyrk, demo, cfg = _kyrk()
    objs = render.pipeline_objects(demo, cfg) + render.campaign_objects(kyrk, demo, cfg)
    assert len(objs) == 4  # pipeline CM + warm-up Job + campaign CM + campaign Job
    for o in objs:
        labels = o["metadata"]["labels"]
        assert labels[key] == value, o["kind"]
        assert labels["htrflow.riksarkivet.se/pipeline"] == "demo-v1"


def _images_campaign(volumes: int, pages: int) -> Campaign:
    """The shape that breaks a count-only split: an `images:` volume is ONE
    line of space-joined URLs, so 300 pages of a 90-character URL is 23 kB on
    that line (`Volume.source_line`)."""
    url = (
        "https://lbiiif.riksarkivet.se/arkis!R00012345/jp2/00000000000000000{:03d}.jpg"
    )
    return Campaign(
        name="kyrk",
        pipeline="demo-v1",
        volumes=[
            Volume(id=f"vol{v:04d}", images=[url.format(p) for p in range(pages)])
            for v in range(volumes)
        ],
    )


def test_200_images_volumes_split_into_configmaps_under_one_mebibyte():
    """The API server caps a ConfigMap's data at 1 MiB. 200 such volumes are
    4.4 MiB in one part when only the count is measured."""
    _, demo, cfg = _kyrk()
    c = _images_campaign(volumes=200, pages=300)
    objects = render.campaign_objects(c, demo, cfg)
    configmaps = objects[::2]

    assert len(configmaps) > 1
    for cm in configmaps:
        assert 0 < len(cm["data"]["volumes.txt"].encode()) <= 1024 * 1024

    rendered = "".join(cm["data"]["volumes.txt"] for cm in configmaps)
    assert rendered == "".join(v.source_line() + "\n" for v in c.volumes)
    assert sum(job["spec"]["completions"] for job in objects[1::2]) == 200


def test_the_split_stem_is_the_same_at_nine_parts_and_at_ten():
    """The stem a split renders under depends on the campaign's NAME, never
    on how many parts this render happens to make. Measured off the part
    count, a campaign crossing 9 -> 10 parts would lose a character, every
    part would be renamed, and the append-only rule would have no earlier
    render to compare against -- an applied campaign would silently be
    deleted and started over."""
    c = Campaign(
        name="a" * 58,
        pipeline="demo-v1",
        volumes=[Volume(id="v1", manifest="https://x/y")],
    )
    part = [[Volume(id=f"v{i}", manifest="https://x/y") for i in range(10_000)]]
    nine = render.campaign_names(c, part * 9)
    ten = render.campaign_names(c, part * 10)

    stems = {name.rsplit("-part", 1)[0] for name in nine + ten}
    assert stems == {render.split_stem(c.name)}
    assert len(render.split_stem(c.name)) == 50
    # The worst case the reservation is for: 999 parts of 10 000 volumes.
    assert len(f"{render.split_stem(c.name)}-part999-9999") == 63


def test_ttl_defaults_to_a_week_and_is_a_converter_yaml_value():
    """The Job is an inspection window over a campaign, not the record of it
    (B76): a day was short enough that a finished campaign was reaped between
    two applies and re-run from the top."""
    kyrk, demo, cfg = _kyrk()
    assert ConverterConfig().ttl_seconds_after_finished == 7 * 24 * 3600
    cfg = cfg.model_copy(update={"ttl_seconds_after_finished": 600})
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    assert job["spec"]["ttlSecondsAfterFinished"] == 600


def test_a_pipeline_may_keep_its_campaigns_jobs_longer():
    kyrk, demo, cfg = _kyrk()
    demo = demo.model_copy(update={"ttl_seconds_after_finished": 99})
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    assert job["spec"]["ttlSecondsAfterFinished"] == 99


def test_the_campaign_configmap_names_the_image_its_volumes_ran_on():
    """The ConfigMap outlives the Job (no TTL, B76), so the digest that
    actually processed these volumes has to be on it -- the Job's own
    `image:` is gone with the Job."""
    kyrk, demo, cfg = _kyrk()
    cm = render.campaign_objects(kyrk, demo, cfg)[0]
    assert cm["metadata"]["annotations"] == {
        "htrflow.riksarkivet.se/image-digest": demo.image,
        "argocd.argoproj.io/hook": "Skip",
    }


# --- apply's own observation of a finished Job (B76 review) --------------


def _live_job(conditions, **status) -> dict:
    return {
        "metadata": {
            "name": "kyrk",
            "namespace": "htr-test",
            "uid": "uid-kyrk-1",
            "labels": {
                "htrflow.riksarkivet.se/campaign": "kyrk",
                "htrflow.riksarkivet.se/pipeline": "demo-v1",
            },
        },
        "spec": {"completions": 3},
        "status": {"conditions": conditions, **status},
    }


def test_a_running_job_has_nothing_to_record():
    """While the campaign runs, the read API sees more than an apply does --
    it reads the pods. Only the END is what an apply must not miss."""
    _, _, cfg = _kyrk()
    job = _live_job([], active=1, succeeded=1)
    assert render.status_configmap(job, cfg) is None


def test_a_completed_job_is_recorded_by_the_apply_itself():
    """The read API writes the record only while someone has the status page
    open. A campaign that finished unwatched and was then reaped would have
    no terminal record at all, and the next apply would run it again."""
    _, _, cfg = _kyrk()
    job = _live_job(
        [{"type": "Complete", "status": "True"}],
        succeeded=3,
        startTime="2026-09-08T08:00:00Z",
        completionTime="2026-09-08T10:00:00Z",
    )
    cm = render.status_configmap(job, cfg)
    assert cm["metadata"]["name"] == "campaign-kyrk-status"
    assert cm["metadata"]["namespace"] == "htr-test"
    assert cm["metadata"]["labels"]["htrflow.riksarkivet.se/kind"] == "status"
    assert cm["metadata"]["labels"]["htrflow.riksarkivet.se/campaign"] == "kyrk"
    assert cm["data"] == {
        "phase": "Succeeded",
        "volumesTotal": "3",
        "volumesDone": "3",
        "volumesFailed": "0",
        "startedAt": "2026-09-08T08:00:00Z",
        "finishedAt": "2026-09-08T10:00:00Z",
        "resultsBase": f"{cfg.public_results_base}/htr-test/demo-v1",
        "jobUid": "uid-kyrk-1",
    }


def test_the_record_names_the_job_it_describes():
    """A Job recreated under the same name -- a reaped campaign whose file
    gained volumes -- is a different run. The uid is what tells the read API
    and the next apply that this record is about the old one (3075)."""
    _, _, cfg = _kyrk()
    job = _live_job([{"type": "Complete", "status": "True"}], succeeded=3)
    job["metadata"]["uid"] = "uid-kyrk-2"
    assert render.status_configmap(job, cfg)["data"]["jobUid"] == "uid-kyrk-2"


def test_the_records_results_base_has_no_doubled_slash():
    """The read API strips a trailing slash off its base; a converter.yaml
    that ends in one wrote a different resultsBase for the same campaign,
    and two managers disagreeing on a value is a conflict (3081)."""
    _, _, cfg = _kyrk()
    cfg = cfg.model_copy(update={"public_results_base": "http://x/htr-results/"})
    job = _live_job([{"type": "Complete", "status": "True"}], succeeded=3)
    data = render.status_configmap(job, cfg)["data"]
    assert data["resultsBase"] == "http://x/htr-results/htr-test/demo-v1"


def test_a_job_that_gave_up_with_some_indexes_done_is_partially_failed():
    """A Failed Job has no completionTime: its condition is the only clock."""
    _, _, cfg = _kyrk()
    job = _live_job(
        [
            {
                "type": "Failed",
                "status": "True",
                "lastTransitionTime": "2026-09-08T09:30:00Z",
            }
        ],
        succeeded=2,
    )
    data = render.status_configmap(job, cfg)["data"]
    assert data["phase"] == "PartiallyFailed"
    assert (data["volumesDone"], data["volumesFailed"]) == ("2", "1")
    assert data["finishedAt"] == "2026-09-08T09:30:00Z"


def test_a_job_that_published_nothing_is_failed():
    _, _, cfg = _kyrk()
    job = _live_job([{"type": "Failed", "status": "True"}])
    assert render.status_configmap(job, cfg)["data"]["phase"] == "Failed"


def test_the_apply_and_the_read_api_write_the_same_field_names():
    """Two writers, one record. The read API observes more (it reads pods,
    so it alone writes `failedVolumes`), but every field the apply writes
    must be one the API writes too, or a merge would carry two spellings of
    the same fact."""
    from htrflow_web import projection

    _, _, cfg = _kyrk()
    job = _live_job([{"type": "Complete", "status": "True"}], succeeded=3)
    theirs = projection.status_record(
        projection.summarize(job, cfg, {"phase": "succeeded"})
    )
    assert set(render.status_configmap(job, cfg)["data"]) == set(theirs)


IIIF_SIZE = (
    "https://lbiiif.riksarkivet.se/arkis!R0001203_{:05d}/full/2500,/0/default.jpg"
)


def test_a_iiif_size_url_survives_converter_shell_and_wrapper(tmp_path):
    """The whole path a URL travels, with the comma that broke it live on
    2026-09-14: `Volume.source_line` -> the ConfigMap's volumes.txt -> the
    Job's own shell (run here, not paraphrased) -> `IMAGES` -> the wrapper's
    split. `/full/2500,/0/default.jpg` is a IIIF Image API size request, so
    the three URLs have to come out as three, whole."""
    config = pytest.importorskip("htrflow_batch.config")
    urls = [IIIF_SIZE.format(p) for p in (44, 45, 46)]
    _, demo, cfg = _kyrk()
    c = Campaign(
        name="kyrk",
        pipeline="demo-v1",
        volumes=[Volume(id="R0001203", images=urls)],
    )
    configmap, job = render.campaign_objects(c, demo, cfg)

    volumes_txt = tmp_path / "volumes.txt"
    volumes_txt.write_text(configmap["data"]["volumes.txt"])
    # The one stand-in: the container's own `python`, which prints what the
    # shell exported instead of running the wrapper. The script is otherwise
    # the rendered one, read straight off the Job.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text('#!/bin/sh\nprintf %s "$IMAGES"\n')
    (bin_dir / "python").chmod(0o755)
    script = job["spec"]["template"]["spec"]["containers"][0]["args"][0].replace(
        "/campaign/volumes.txt", str(volumes_txt)
    )
    result = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "JOB_COMPLETION_INDEX": "0",
            "HOME": str(tmp_path / "home"),
            "TMPDIR": str(tmp_path / "tmp"),
            "YOLO_CONFIG_DIR": str(tmp_path / "yolo"),
        },
    )
    assert result.returncode == 0, result.stderr

    images = config.Config.model_construct(images=result.stdout)
    assert images.image_urls == urls


# -- the private-model token (2026-09-14) ----------------------------------


def _warmup_env(cfg: ConverterConfig, demo) -> list[dict]:
    warmup = render.pipeline_objects(demo, cfg)[1]
    return warmup["spec"]["template"]["spec"]["containers"][0]["env"]


def test_no_hf_token_secret_means_no_token_env_anywhere():
    kyrk, demo, cfg = _kyrk()
    assert cfg.hf_token_secret == ""
    assert "HF_TOKEN" not in [e["name"] for e in _warmup_env(cfg, demo)]


def test_the_warmup_job_reads_hf_token_from_the_named_secret():
    """A private model is downloaded once, by the one pod the NetworkPolicy
    lets reach the Hub. `optional: false` so a missing Secret stops the pod
    from starting: an anonymous retry would fail on the download anyway, and
    later than this."""
    kyrk, demo, cfg = _kyrk()
    cfg = cfg.model_copy(update={"hf_token_secret": "htr-batch-hf"})
    token = next(e for e in _warmup_env(cfg, demo) if e["name"] == "HF_TOKEN")
    assert token["valueFrom"]["secretKeyRef"] == {
        "name": "htr-batch-hf",
        "key": "token",
        "optional": False,
    }
    assert "value" not in token  # the value stays in the Secret


def test_the_campaign_job_never_gets_the_hub_token():
    """Offline by design: a campaign pod runs HF_HUB_OFFLINE=1 against the
    cache the warm-up filled and has no egress to the Hub, so a token there
    would be a credential in a pod that cannot use it."""
    kyrk, demo, cfg = _kyrk()
    cfg = cfg.model_copy(update={"hf_token_secret": "htr-batch-hf"})
    # Every object the campaign renders to, not just the first part's Job: a
    # split campaign has several, and the ConfigMap beside each one.
    rendered = yaml.safe_dump_all(render.campaign_objects(kyrk, demo, cfg))
    assert "HF_TOKEN" not in rendered
    assert "htr-batch-hf" not in rendered


def _cache_mounts(pod_spec: dict) -> list[dict]:
    """Every mount of the model-cache volume, init containers included."""
    cache = next(v["name"] for v in pod_spec["volumes"] if "persistentVolumeClaim" in v)
    return [
        m
        for c in pod_spec.get("initContainers", []) + pod_spec["containers"]
        for m in c["volumeMounts"]
        if m["name"] == cache
    ]


def _warmup_pod(p, cfg) -> dict:
    return render.pipeline_objects(p, cfg)[1]["spec"]["template"]


def test_a_recipe_edit_replaces_the_warmup_and_moves_the_cache_it_fills():
    """audit 0923 C-3: the warm-up and its marker were keyed by pipeline id.
    A steps-only edit rendered a byte-identical warm-up Job, so the apply was
    a no-op, the new model was never downloaded, and `<id>.done` was still
    there: new campaign pods passed the gate and failed every index, offline,
    without the model. An image edit raced the same way. Any recipe change
    now changes the warm-up's pod template (the apply replaces it) and the
    directory both it and the campaign pods use."""
    kyrk, demo, cfg = _kyrk()
    steps = [*demo.steps[:1], {"step": "TextRecognition", "settings": {"model": "x"}}]
    for edited in (
        demo.model_copy(update={"steps": steps}),
        demo.model_copy(update={"image": demo.image[:-1] + "b"}),
    ):
        assert _warmup_pod(edited, cfg) != _warmup_pod(demo, cfg)
        before = render.campaign_objects(kyrk, demo, cfg)[1]["spec"]["template"]
        after = render.campaign_objects(kyrk, edited, cfg)[1]["spec"]["template"]
        paths = {m["subPath"] for m in _cache_mounts(after["spec"])}
        assert paths.isdisjoint(m["subPath"] for m in _cache_mounts(before["spec"]))
        assert paths == {
            m["subPath"] for m in _cache_mounts(_warmup_pod(edited, cfg)["spec"])
        }


def test_each_pipeline_warms_and_reads_a_cache_directory_of_its_own():
    """audit 0923 S-2: every warm-up mounted the whole cache PVC read-write,
    so one pipeline's warm-up -- which runs its author's pinned YOLO `.pt`,
    a pickle -- could overwrite another pipeline's snapshots and markers,
    which that pipeline's campaigns then load offline, trusting the cache.
    A warm-up writes only its own recipe's directory; the campaign pods read
    that directory alone, read-only."""
    kyrk, demo, cfg = _kyrk()
    other = demo.model_copy(update={"id": "other-v1"})
    same_recipe = demo.model_copy(update={"id": "copy-v1"})
    dirs = set()
    for p in (demo, other, same_recipe):
        warm = _cache_mounts(_warmup_pod(p, cfg)["spec"])
        assert [m.get("readOnly", False) for m in warm] == [False]
        (path,) = {m["subPath"] for m in warm}
        assert path.startswith(f"{p.id}-")
        pod = render.campaign_objects(kyrk, p, cfg)[1]["spec"]["template"]["spec"]
        reads = _cache_mounts(pod)
        assert len(reads) == 2  # the warmup-wait gate and the wrapper
        assert all(m["readOnly"] is True and m["subPath"] == path for m in reads)
        dirs.add(path)
    # one recipe under two ids is two authors: no directory is shared
    assert len(dirs) == 3


def test_the_warmup_pod_template_names_its_recipe():
    _, demo, cfg = _kyrk()
    annotations = _warmup_pod(demo, cfg)["metadata"]["annotations"]
    assert annotations["htrflow.riksarkivet.se/recipe-sha256"] == demo.recipe_sha256


def _wrapper_env(job: dict) -> dict:
    return {
        e["name"]: e for e in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }


def test_the_wrapper_is_told_which_attempt_of_its_index_it_is(monkeypatch):
    """audit 0923 W-4 (the wrapper's request): the wrapper fails a page it
    still defers on its index's LAST attempt, so it needs the attempt it is
    on and how many there are. The Job controller annotates every pod of an
    Indexed Job with `backoffLimitPerIndex` set with
    `batch.kubernetes.io/job-index-failure-count` (kubernetes
    pkg/controller/job: `addIndexFailureCountAnnotation`), which the
    downward API reads; the limit is rendered from the very field the Job
    carries, so the two cannot drift."""
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    env = _wrapper_env(job)
    assert env["INDEX_FAILURE_COUNT"]["valueFrom"] == {
        "fieldRef": {
            "fieldPath": "metadata.annotations"
            "['batch.kubernetes.io/job-index-failure-count']"
        }
    }
    assert env["BACKOFF_LIMIT_PER_INDEX"]["value"] == str(
        job["spec"]["backoffLimitPerIndex"]
    )

    skeleton = render._base("campaign-job.yaml")
    monkeypatch.setitem(skeleton["spec"], "backoffLimitPerIndex", 5)
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    assert job["spec"]["backoffLimitPerIndex"] == 5
    assert _wrapper_env(job)["BACKOFF_LIMIT_PER_INDEX"]["value"] == "5"
