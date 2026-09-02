import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from htrflow_converter import render
from htrflow_converter.models import Campaign, Volume
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


def test_init_container_present_with_pipeline_marker_path():
    kyrk, demo, cfg = _kyrk()
    job = render.campaign_objects(kyrk, demo, cfg)[1]
    init = job["spec"]["template"]["spec"]["initContainers"]
    assert len(init) == 1
    assert init[0]["name"] == "warmup-wait"
    assert init[0]["image"] == demo.image
    assert f"/data/warmup/{demo.id}.done" in init[0]["command"][-1]
    assert "nvidia.com/gpu" not in init[0]["resources"]["requests"]


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


def test_legacy_layout_flips_s3_prefix():
    _, demo, cfg = _kyrk()
    v = [Volume(id="v1", manifest="https://x/y")]
    c = Campaign(name="kyrk", pipeline="demo-v1", volumes=v)

    def s3_prefix(cfg_):
        job = render.campaign_objects(c, demo, cfg_)[1]
        env = job["spec"]["template"]["spec"]["containers"][0]["env"]
        return next(e["value"] for e in env if e["name"] == "S3_PREFIX")

    assert s3_prefix(cfg) == f"{cfg.namespace}/"
    assert s3_prefix(cfg.model_copy(update={"legacy_layout": True})) == ""


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
    _, demo, cfg = _kyrk()
    name = "a" * 58
    volumes = [
        Volume(id=f"v{i}", manifest=f"https://example.org/{i}") for i in range(10_001)
    ]
    c = Campaign(name=name, pipeline="demo-v1", volumes=volumes)
    cm1, job1, cm2, job2 = render.campaign_objects(c, demo, cfg)

    assert job1["metadata"]["name"] != job2["metadata"]["name"]
    assert job1["metadata"]["name"] == f"{name}-part1"
    assert job2["metadata"]["name"] == f"{name}-part2"
    assert cm1["metadata"]["name"] == f"campaign-{name}-part1"
    assert cm2["metadata"]["name"] == f"campaign-{name}-part2"

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
    (scripts/kueue-pause-sync.sh) is what makes it stick under Kueue."""
    kyrk, demo, cfg = _kyrk()
    assert "suspend" not in render.campaign_objects(kyrk, demo, cfg)[1]["spec"]
    paused = kyrk.model_copy(update={"suspend": True})
    assert render.campaign_objects(paused, demo, cfg)[1]["spec"]["suspend"] is True


def test_pipeline_max_seconds_overrides_the_converter_default():
    kyrk, demo, cfg = _kyrk()

    def max_seconds(pipeline):
        job = render.campaign_objects(kyrk, pipeline, cfg)[1]
        env = job["spec"]["template"]["spec"]["containers"][0]["env"]
        return next(e["value"] for e in env if e["name"] == "MAX_SECONDS")

    assert max_seconds(demo) == str(cfg.max_seconds)
    assert max_seconds(demo.model_copy(update={"max_seconds": 60})) == "60"


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
    """`kubectl apply --prune -l <CAMPAIGN_SELECTOR>` (and Argo CD's prune) is
    what makes "deleting a campaign file cancels the campaign" true. An object
    without the label survives its own deletion: the campaign ConfigMap did,
    and outlived the Job it fed. `cli.py` passes this same constant to
    `kubectl`, so the label and the selector can never drift apart."""
    assert render.CAMPAIGN_SELECTOR == "htrflow.riksarkivet.se/managed-by=converter"
    key, _, value = render.CAMPAIGN_SELECTOR.partition("=")
    kyrk, demo, cfg = _kyrk()
    objs = render.pipeline_objects(demo, cfg) + render.campaign_objects(kyrk, demo, cfg)
    assert len(objs) == 4  # pipeline CM + warm-up Job + campaign CM + campaign Job
    for o in objs:
        labels = o["metadata"]["labels"]
        assert labels[key] == value, o["kind"]
        assert labels["htrflow.riksarkivet.se/pipeline"] == "demo-v1"
