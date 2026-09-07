import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from htrflow_converter import render
from htrflow_converter.models import Campaign, ConverterConfig, Volume
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
    assert spec["ttlSecondsAfterFinished"] == 86400
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
    assert "annotations" not in job["metadata"]  # no partial admission (B63)
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


def test_the_warmup_wait_is_bounded_and_fails_the_index():
    """An unbounded `until [ -f … ]` holds `nvidia.com/gpu: 1` for the pod's
    whole deadline, once per retry, whenever a pipeline's warm-up never wrote
    its marker (audit X3). The init container now gives up after
    `warmup_wait_seconds`, says on stderr which marker it waited for, and
    exits 13 -- which the Job's podFailurePolicy turns into a failed index
    instead of three more six-hour waits."""
    kyrk, demo, cfg = _kyrk()
    cfg = cfg.model_copy(update={"warmup_wait_seconds": 120})
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    script = job["spec"]["template"]["spec"]["initContainers"][0]["command"][-1]
    marker = f"/data/warmup/{demo.id}.done"

    assert f"[ -f {marker} ]" in script
    # `-le`, not `-lt`: the check runs BEFORE each sleep, so `-lt` gives up
    # one step early -- at 110 s here, while printing "after 120s".
    assert '[ "$n" -le 120 ]' in script
    assert "exit 13" in script
    message = script.split("echo ", 1)[1].split(" >&2", 1)[0]
    assert marker in message

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
                {"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}
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
                {"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}
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
    line of comma-joined URLs, so 300 pages of a 90-character URL is 23 kB on
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
