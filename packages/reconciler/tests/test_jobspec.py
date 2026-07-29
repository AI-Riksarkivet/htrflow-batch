from htrflow_reconciler.jobspec import ReconcilerConfig, build_job
from htrflow_reconciler.models import PipelineSpec, Volume
from htrflow_reconciler.status import job_name

P = PipelineSpec(
    id="demo-v1",
    image="r/i@sha256:abc",
    steps_yaml="steps: []\n",
    steps_sha256="s" * 64,
)
V = Volume(
    id="R0001203", manifest_url="https://lbiiif.riksarkivet.se/arkis!R0001203/manifest"
)
CFG = ReconcilerConfig(public_results_base="http://localhost:30900/htr-results")


def _env(job):
    c = job["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in c["env"]}


def test_job_identity_and_queue():
    job = build_job(P, V, V.manifest_url, CFG)
    assert job["metadata"]["name"] == job_name("demo-v1", "R0001203")
    assert job["metadata"]["namespace"] == "htr-batch"
    assert job["metadata"]["labels"]["kueue.x-k8s.io/queue-name"] == "htr-batch"
    assert job["spec"]["suspend"] is True
    assert job["spec"]["backoffLimit"] == 0


def test_job_env_carries_provenance():
    env = _env(build_job(P, V, V.manifest_url, CFG))
    assert env["VOLUME_REF"] == "R0001203"
    assert env["PIPELINE_ID"] == "demo-v1"
    assert env["IMAGE_DIGEST"] == "r/i@sha256:abc"
    assert env["IIIF_MANIFEST_URL"] == V.manifest_url
    assert env["PUBLIC_RESULTS_BASE"] == "http://localhost:30900/htr-results"


def test_job_image_from_pipeline_pin():
    c = build_job(P, V, V.manifest_url, CFG)["spec"]["template"]["spec"]["containers"][
        0
    ]
    assert c["image"] == "r/i@sha256:abc"
    assert c["resources"]["limits"]["nvidia.com/gpu"] == "1"


def test_job_mounts_pipeline_configmap():
    vols = build_job(P, V, V.manifest_url, CFG)["spec"]["template"]["spec"]["volumes"]
    byname = {v["name"]: v for v in vols}
    assert byname["pipeline"]["configMap"]["name"] == "htr-pipeline-demo-v1"
    assert byname["data"]["persistentVolumeClaim"]["claimName"] == "htr-test-data"
