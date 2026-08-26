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


def test_job_is_labelled_managed_by_reconciler():
    """The reconciler's window selector keys off this label: hand-run Jobs carry
    ``app=htrflow-batch`` too (the operators' selectors need it) and have no
    TTL, so only the managed-by label keeps them out of the in-flight count."""
    labels = build_job(P, V, V.manifest_url, CFG)["metadata"]["labels"]
    assert labels["batch.htrflow/managed-by"] == "reconciler"
    assert labels["app"] == "htrflow-batch"


def test_job_env_carries_provenance():
    env = _env(build_job(P, V, V.manifest_url, CFG))
    assert env["VOLUME_REF"] == "R0001203"
    assert env["PIPELINE_ID"] == "demo-v1"
    assert env["IMAGE_DIGEST"] == "r/i@sha256:abc"
    assert env["IIIF_MANIFEST_URL"] == V.manifest_url
    assert env["PUBLIC_RESULTS_BASE"] == "http://localhost:30900/htr-results"
    # Pinned empty so results land where s3.manifest_key looks for them,
    # whatever the S3 secret's envFrom carries.
    assert env["S3_PREFIX"] == ""


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


def _pod(job):
    return job["spec"]["template"]["spec"]


def _container(job):
    return _pod(job)["containers"][0]


def test_job_pod_meets_pod_security_restricted():
    """D14: the GPU pod runs as an unprivileged user with no way up — the
    fields PSA ``restricted`` checks, plus no API credential in the pod."""
    job = build_job(P, V, V.manifest_url, CFG)
    pod, c = _pod(job), _container(job)
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "fsGroup": 1000,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert c["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert pod["runtimeClassName"] == "nvidia"


def test_job_model_cache_is_read_only_and_offline():
    """Batch Jobs never write the shared cache and never reach HF Hub: the
    warm-up Job is the only writer, so a compromised Job cannot poison the
    models every later Job loads."""
    job = build_job(P, V, V.manifest_url, CFG)
    mounts = {m["name"]: m for m in _container(job)["volumeMounts"]}
    assert mounts["data"]["readOnly"] is True
    env = _env(job)
    assert env["HF_HOME"] == "/data/hf"
    assert env["HF_HUB_OFFLINE"] == "1"


def test_job_writable_paths_live_on_the_tmpfs_workdir():
    """readOnlyRootFilesystem + non-root: everything htrflow's stack writes
    outside HF_HOME (ultralytics settings, triton/inductor JIT caches, temp
    files) must land on /work, the only writable, size-bounded mount."""
    env = _env(build_job(P, V, V.manifest_url, CFG))
    assert env["HOME"] == "/work/home"
    assert env["TMPDIR"] == "/work/tmp"
    assert env["YOLO_CONFIG_DIR"] == "/work/ultralytics"


def test_job_reads_s3_credentials_from_a_mounted_file():
    """Secret material is a file, not process env: env leaks through
    ``kubectl describe``, crash dumps and every child process."""
    job = build_job(P, V, V.manifest_url, CFG)
    c = _container(job)
    assert "envFrom" not in c
    env = {e["name"]: e for e in c["env"]}
    assert env["AWS_SHARED_CREDENTIALS_FILE"]["value"] == "/secrets/s3/credentials"
    assert env["S3_ENDPOINT"]["valueFrom"]["secretKeyRef"] == {
        "name": "htr-batch-s3",
        "key": "S3_ENDPOINT",
        "optional": True,
    }
    assert env["S3_BUCKET"]["valueFrom"]["secretKeyRef"] == {
        "name": "htr-batch-s3",
        "key": "S3_BUCKET",
    }
    assert all(not e["name"].startswith("AWS_ACCESS") for e in c["env"])
    assert all(not e["name"].startswith("AWS_SECRET") for e in c["env"])
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["s3"] == {"name": "s3", "mountPath": "/secrets/s3", "readOnly": True}
    vols = {v["name"]: v for v in _pod(job)["volumes"]}
    assert vols["s3"]["secret"] == {"secretName": "htr-batch-s3", "defaultMode": 0o440}


def test_warmup_job_is_the_only_cache_writer():
    """One warm-up Job per pipeline fills the cache the batch Jobs read: same
    image and pipeline ConfigMap (so it downloads exactly what the pipeline
    loads), RW cache, HF Hub reachable, no GPU, outside the Kueue queue."""
    from htrflow_reconciler.jobspec import build_warmup_job, warmup_job_name

    job = build_warmup_job(P, CFG)
    assert job["metadata"]["name"] == warmup_job_name("demo-v1") == "htr-warmup-demo-v1"
    labels = job["metadata"]["labels"]
    assert labels["app"] == "htrflow-warmup"
    assert labels["batch.htrflow/managed-by"] == "reconciler"
    assert labels["batch.htrflow/pipeline"] == "demo-v1"
    assert "kueue.x-k8s.io/queue-name" not in labels
    assert "suspend" not in job["spec"]
    assert job["spec"]["backoffLimit"] == 2
    assert "ttlSecondsAfterFinished" not in job["spec"]
    pod, c = _pod(job), _container(job)
    assert c["image"] == "r/i@sha256:abc"
    assert c["command"] == ["python", "-m", "htrflow_batch.warmup"]
    assert "nvidia.com/gpu" not in c["resources"]["limits"]
    assert "runtimeClassName" not in pod
    env = _env(job)
    assert env["PIPELINE_PATH"] == "/config/pipeline.yaml"
    assert env["HF_HOME"] == "/data/hf"
    assert "HF_HUB_OFFLINE" not in env
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert env["HOME"] == "/work/home"
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["data"].get("readOnly", False) is False
    assert "s3" not in mounts
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["pipeline"]["configMap"]["name"] == "htr-pipeline-demo-v1"
    assert vols["data"]["persistentVolumeClaim"]["claimName"] == "htr-test-data"
    assert vols["work"]["emptyDir"] == {"sizeLimit": "4Gi"}


def test_warmup_job_pod_meets_pod_security_restricted():
    from htrflow_reconciler.jobspec import build_warmup_job

    job = build_warmup_job(P, CFG)
    assert _pod(job)["automountServiceAccountToken"] is False
    assert _pod(job)["securityContext"]["runAsNonRoot"] is True
    assert _container(job)["securityContext"]["readOnlyRootFilesystem"] is True


# -- O2: the Job contract the docs describe -----------------------------------

POD_FAILURE_POLICY = {
    "rules": [
        {"action": "Ignore", "onPodConditions": [{"type": "DisruptionTarget"}]},
        {
            "action": "FailJob",
            "onExitCodes": {
                "containerName": "wrapper",
                "operator": "In",
                "values": [13],
            },
        },
    ]
}


def test_job_pod_failure_policy_ignores_disruptions_and_fails_on_13():
    """A drain/preemption does not consume an attempt (Ignore on
    DisruptionTarget); exit 13 fails the Job at once, so the Failed condition
    carries reason PodFailurePolicy even after the pod is gone (R6)."""
    job = build_job(P, V, V.manifest_url, CFG)
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["podFailurePolicy"] == POD_FAILURE_POLICY


def test_warmup_job_pod_failure_policy_targets_its_own_container():
    from htrflow_reconciler.jobspec import build_warmup_job

    rules = build_warmup_job(P, CFG)["spec"]["podFailurePolicy"]["rules"]
    assert rules[1]["onExitCodes"]["containerName"] == "warmup"


def test_job_deadline_scales_with_page_count():
    """Measured ~13 s/page on the GB10: a flat 6 h deadline cannot finish a
    1 650-page volume. max(min, pages x per_page), min when unknown."""
    cfg = ReconcilerConfig(
        public_results_base="http://x",
        job_min_deadline_seconds=21600,
        job_seconds_per_page=30,
    )
    assert (
        build_job(P, V, V.manifest_url, cfg)["spec"]["activeDeadlineSeconds"] == 21600
    )
    small = build_job(P, V, V.manifest_url, cfg, page_count=100)
    assert small["spec"]["activeDeadlineSeconds"] == 21600
    big = build_job(P, V, V.manifest_url, cfg, page_count=1000)
    assert big["spec"]["activeDeadlineSeconds"] == 30000


def test_job_placement_comes_from_config():
    cfg = ReconcilerConfig(
        public_results_base="http://x",
        job_runtime_class="nvidia-cdi",
        job_node_selector={"gpu": "gb10"},
        job_tolerations=[{"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}],
    )
    pod = _pod(build_job(P, V, V.manifest_url, cfg))
    assert pod["runtimeClassName"] == "nvidia-cdi"
    assert pod["nodeSelector"] == {"gpu": "gb10"}
    assert pod["tolerations"] == [
        {"key": "gpu", "operator": "Exists", "effect": "NoSchedule"}
    ]
    default = _pod(build_job(P, V, V.manifest_url, CFG))
    assert default["runtimeClassName"] == "nvidia"
    assert "nodeSelector" not in default and "tolerations" not in default


def test_empty_runtime_class_omits_the_field():
    cfg = ReconcilerConfig(public_results_base="http://x", job_runtime_class="")
    assert "runtimeClassName" not in _pod(build_job(P, V, V.manifest_url, cfg))
