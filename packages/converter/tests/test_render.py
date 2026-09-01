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
    assert job["metadata"]["annotations"] == {"kueue.x-k8s.io/job-min-parallelism": "1"}
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
