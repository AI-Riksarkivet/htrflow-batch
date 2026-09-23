"""The chart's Kyverno policies, run against the objects they exist to refuse.

test_chart_render.py asserts on the *shape* of a rendered policy -- which
kinds a rule matches, which field a condition reads. That cannot say whether
the JMESPath inside actually refuses a given object, and every bypass the
2026-09-17 audit found was a policy of the right shape that let the wrong
object through. So these render the policies exactly as an install does and
put a refused object and an admitted one through the Kyverno CLI, the same
binary a campaigns repo's CI runs (`render.yml`, `KYVERNO_VERSION`).

The CLI stands in for the admission request: `--userinfo` names the
identity asking, and a values file sets `request.operation` and, for an
UPDATE, `request.oldObject` -- the stored object the write replaces (see
`_replacing` for how the CLI treats that value).
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CHART = REPO / "charts" / "htrflow-batch"
NAMESPACE = "htr-batch"
DIGEST = "sha256:" + "a" * 64
#: `ci/full-values.yaml`'s allow-list, which every render below starts from.
ALLOWED = "ghcr.io/riksarkivet"
APPLY_SA = f"system:serviceaccount:{NAMESPACE}:htrflow-campaigns"

pytestmark = [
    pytest.mark.skipif(shutil.which("helm") is None, reason="helm not on PATH"),
    pytest.mark.skipif(shutil.which("kyverno") is None, reason="kyverno not on PATH"),
]

_COUNTS = re.compile(
    r"pass: (\d+), fail: (\d+), warn: (\d+), error: (\d+), skip: (\d+)"
)


def render_policy(
    tmp_path: Path, template: str, *sets: str, values: str = "ci/full-values.yaml"
) -> Path:
    """One policy template, rendered the way an install renders it."""
    cmd = [
        "helm",
        "template",
        "htr",
        str(CHART),
        "-n",
        NAMESPACE,
        "-f",
        str(CHART / values),
    ]
    for setting in sets:
        cmd += ["--set", setting]
    cmd += ["--show-only", f"templates/policies/{template}.yaml"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    path = tmp_path / f"policy-{template}.yaml"
    path.write_text(result.stdout, encoding="utf-8")
    return path


def admission(
    tmp_path: Path,
    policy: Path,
    resource: dict,
    *,
    user: str | None = None,
    operation: str = "CREATE",
    old: dict | None = None,
    cluster: list[dict] | None = None,
) -> tuple[str, str]:
    """("refused" | "admitted" | "not matched", CLI output) for one request.

    An error is neither verdict -- a policy the CLI cannot evaluate would
    otherwise read as "admitted" -- so it fails the test outright.

    ``cluster`` is what a rule's `apiCall` finds on the API server: the CLI
    answers those from the resources it was given, so these go into the
    same file after ``resource``. They are checked too; a test on
    ``resource`` alone reads the lines that name it.
    """
    res = tmp_path / "resource.yaml"
    res.write_text(yaml.safe_dump_all([resource, *(cluster or [])]), encoding="utf-8")
    cmd = ["kyverno", "apply", str(policy), "--resource", str(res), "--remove-color"]
    global_values: dict = {"request.operation": operation}
    if old is not None:
        global_values["request.oldObject"] = _replacing(resource, old)
    doc: dict = {
        "apiVersion": "cli.kyverno.io/v1alpha1",
        "kind": "Value",
        "metadata": {"name": "request"},
        "globalValues": global_values,
    }
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump(doc), encoding="utf-8")
    cmd += ["--values-file", str(values)]
    if user is not None:
        info = tmp_path / "userinfo.yaml"
        info.write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "cli.kyverno.io/v1alpha1",
                    "kind": "UserInfo",
                    "metadata": {"name": "asker"},
                    "userInfo": {"username": user},
                }
            ),
            encoding="utf-8",
        )
        cmd += ["--userinfo", str(info)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    out = result.stdout + result.stderr
    counts = _COUNTS.search(out)
    assert counts, out
    passed, failed, _, errored, _ = map(int, counts.groups())
    assert errored == 0, out
    if failed:
        assert result.returncode != 0, out
        return "refused", out
    assert result.returncode == 0, out
    return ("admitted" if passed else "not matched"), out


def _replacing(new: dict, old: dict) -> dict:
    """`old` as a values entry that turns `new` INTO `old`.

    The CLI starts `request.oldObject` from the resource under test and
    deep-merges the values over it, so a key `old` simply lacks -- a label
    the update adds -- would survive the merge. Each such key is sent as
    null, which the merge does apply."""
    out: dict = {key: None for key in new if key not in old}
    for key, value in old.items():
        both = isinstance(value, dict) and isinstance(new.get(key), dict)
        out[key] = _replacing(new[key], value) if both else value
    return out


def configmap(name: str, labels: dict | None = None, data: dict | None = None) -> dict:
    meta: dict = {"name": name, "namespace": NAMESPACE}
    if labels is not None:
        meta["labels"] = labels
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": meta,
        "data": data or {},
    }


CONVERTER = {"htrflow.riksarkivet.se/managed-by": "converter"}


# --- the harness itself: the rule that was already right stays right -------


def test_the_apply_identity_still_may_not_delete_a_foreign_object(tmp_path: Path):
    policy = render_policy(tmp_path, "rbac-scope", "apply.rbac.enabled=true")
    foreign = configmap("team-settings")
    verdict, _ = admission(tmp_path, policy, foreign, user=APPLY_SA, operation="DELETE")
    assert verdict == "refused"
    ours = configmap("campaign-kyrk", CONVERTER)
    verdict, out = admission(tmp_path, policy, ours, user=APPLY_SA, operation="DELETE")
    assert verdict == "admitted", out


# --- T-2: the read API writes its status ConfigMaps and nothing else ------

WEB_SA = f"system:serviceaccount:{NAMESPACE}:htrflow-web"


@pytest.mark.parametrize(
    "name,operation",
    [
        ("htr-pipeline-demo-v1", "CREATE"),
        ("htr-pipeline-demo-v1", "UPDATE"),
        ("campaign-kyrk", "UPDATE"),
    ],
)
def test_the_read_api_cannot_write_a_pipeline_or_a_campaign(
    tmp_path: Path, name: str, operation: str
):
    """The web Role's create/patch covers every ConfigMap, pipeline ones
    included, and the pod serving it has no authentication of its own.
    Overwriting `htr-pipeline-<id>` would choose the weights the next
    campaign loads; overwriting `campaign-<name>` its volumes."""
    policy = render_policy(tmp_path, "rbac-scope")
    target = configmap(name, CONVERTER, data={"x": "y"})
    old = target if operation == "UPDATE" else None
    verdict, out = admission(
        tmp_path, policy, target, user=WEB_SA, operation=operation, old=old
    )
    assert verdict == "refused", out


@pytest.mark.parametrize("operation", ["CREATE", "UPDATE"])
def test_the_read_api_writes_a_campaign_status(tmp_path: Path, operation: str):
    policy = render_policy(tmp_path, "rbac-scope")
    status = configmap("campaign-kyrk-status", data={"state": "done"})
    old = status if operation == "UPDATE" else None
    verdict, out = admission(
        tmp_path, policy, status, user=WEB_SA, operation=operation, old=old
    )
    assert verdict == "admitted", out


# --- 3058: keys beside model_settings override it -------------------------

REVISION = "0123456789abcdef0123456789abcdef01234567"


def pipeline(*steps: dict) -> dict:
    return configmap(
        "htr-pipeline-demo-v1",
        data={"pipeline.yaml": yaml.safe_dump({"steps": list(steps)})},
    )


YOLO = {
    "step": "Segmentation",
    "settings": {
        "model": "yolo",
        "model_settings": {
            "model": "Riksarkivet/yolov9-regions-1",
            "revision": REVISION,
        },
    },
}
TROCR = {
    "step": "TextRecognition",
    "settings": {
        "model": "TrOCR",
        "model_settings": {
            "model": "Riksarkivet/trocr-base-handwritten-hist-swe-2",
            "model_kwargs": {"revision": REVISION},
            "processor_kwargs": {"revision": REVISION},
        },
        "generation_settings": {"batch_size": 8},
    },
}
EXPORT = {"step": "Export", "settings": {"dest": "out", "format": "alto"}}


def test_a_pinned_pipeline_is_admitted(tmp_path: Path):
    policy = render_policy(tmp_path, "model-revision")
    verdict, out = admission(tmp_path, policy, pipeline(YOLO, TROCR, EXPORT))
    assert verdict == "admitted", out


@pytest.mark.parametrize(
    "step,stray",
    [
        # htrflow builds a model's arguments as `model_settings | settings`,
        # so a key beside model_settings wins: YOLO loads the head of the
        # repo, and TrOCR's from_pretrained gets no revision at all.
        (YOLO, {"revision": None}),
        (TROCR, {"model_kwargs": {}}),
    ],
    ids=["yolo", "trocr"],
)
def test_a_key_beside_model_settings_cannot_unpin_the_model(
    tmp_path: Path, step: dict, stray: dict
):
    policy = render_policy(tmp_path, "model-revision")
    bypass = {**step, "settings": {**step["settings"], **stray}}
    verdict, out = admission(tmp_path, policy, pipeline(bypass))
    assert verdict == "refused", out
    assert next(iter(stray)) in out


# --- D-6: a Hugging Face model's processor is a second download -----------


@pytest.mark.parametrize("loader", ["TrOCR", "WordLevelTrOCR", "Donut", "DiT", "trocr"])
@pytest.mark.parametrize(
    "processor_kwargs",
    [None, {}, {"revision": "main"}],
    ids=["absent", "empty", "branch"],
)
def test_a_processor_loaded_from_the_hub_is_pinned_too(
    tmp_path: Path, loader: str, processor_kwargs: dict | None
):
    """htrflow's TrOCR, WordLevelTrOCR, Donut and DiT load the processor
    (tokenizer, image processor) with `processor or model` and
    `processor_kwargs` -- never `model_kwargs`. A pin under model_kwargs
    alone left the processor on the repo's mutable head. htrflow resolves
    the loader by its lower-cased name, so the rule does too."""
    policy = render_policy(tmp_path, "model-revision")
    model_settings = {
        "model": "Riksarkivet/trocr-base-handwritten-hist-swe-2",
        "model_kwargs": {"revision": REVISION},
    }
    if processor_kwargs is not None:
        model_settings["processor_kwargs"] = processor_kwargs
    step = {
        "step": "TextRecognition",
        "settings": {"model": loader, "model_settings": model_settings},
    }
    verdict, out = admission(tmp_path, policy, pipeline(step))
    assert verdict == "refused", out
    assert "processor_kwargs" in out
    model_settings["processor_kwargs"] = {"revision": REVISION}
    verdict, out = admission(tmp_path, policy, pipeline(step))
    assert verdict == "admitted", out


def test_a_loader_without_a_processor_needs_no_processor_pin(tmp_path: Path):
    """YOLO downloads one weights file; it has no processor to pin."""
    policy = render_policy(tmp_path, "model-revision")
    verdict, out = admission(tmp_path, policy, pipeline(YOLO))
    assert verdict == "admitted", out


# --- D-7: a pipeline in binaryData is a pipeline the rule cannot read ------


@pytest.mark.parametrize(
    "name,binary",
    [
        ("htr-pipeline-demo-v1", {"pipeline.yaml"}),
        ("htr-pipeline-demo-v1", {"weights.bin"}),
        ("team-settings", {"pipeline.yaml"}),
    ],
    ids=["pipeline-key", "pipeline-configmap-other-key", "pipeline-key-elsewhere"],
)
def test_a_pipeline_cannot_hide_in_binary_data(tmp_path: Path, name: str, binary: set):
    """The rule reads `data."pipeline.yaml"`. A ConfigMap carrying the same
    key under `binaryData` parsed as `steps: []` and was admitted, and a
    Job mounting it sees the same file -- unpinned models and all."""
    policy = render_policy(tmp_path, "model-revision")
    unpinned = yaml.safe_dump(
        {
            "steps": [
                {
                    **YOLO,
                    "settings": {
                        "model": "yolo",
                        "model_settings": {"model": "Riksarkivet/yolov9-regions-1"},
                    },
                }
            ]
        }
    )
    hidden = configmap(name)
    hidden["binaryData"] = {
        key: base64.b64encode(unpinned.encode()).decode() for key in binary
    }
    verdict, out = admission(tmp_path, policy, hidden)
    assert verdict == "refused", out
    assert "binaryData" in out


def test_other_binary_data_is_still_admitted(tmp_path: Path):
    policy = render_policy(tmp_path, "model-revision")
    other = configmap("team-settings")
    other["binaryData"] = {"logo.png": "iVBORw0KGgo="}
    verdict, out = admission(tmp_path, policy, other)
    assert verdict == "admitted", out


# --- 3061: an image volume is an image too ----------------------------------

OURS = f"{ALLOWED}/htrflow-batch@{DIGEST}"
FOREIGN = f"ghcr.io/attacker/weights@{DIGEST}"


def pod_spec(volume_image: str | None) -> dict:
    volumes: list[dict] = [{"name": "config", "configMap": {"name": "x"}}]
    if volume_image is not None:
        volumes.append({"name": "weights", "image": {"reference": volume_image}})
    return {
        "containers": [
            {
                "name": "main",
                "image": OURS,
                "volumeMounts": [{"name": "config", "mountPath": "/c"}],
            }
        ],
        "volumes": volumes,
    }


def pod(volume_image: str | None) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "p", "namespace": NAMESPACE},
        "spec": pod_spec(volume_image),
    }


def job(volume_image: str | None) -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "j", "namespace": NAMESPACE},
        "spec": {
            "template": {"spec": {**pod_spec(volume_image), "restartPolicy": "Never"}}
        },
    }


@pytest.mark.parametrize("kind", [pod, job], ids=["pod", "job"])
@pytest.mark.parametrize(
    "template,image",
    [
        ("images-allowed", FOREIGN),
        ("images-pinned", f"{ALLOWED}/weights:latest"),
    ],
    ids=["foreign", "unpinned"],
)
def test_an_image_volume_is_held_to_the_image_rules(
    tmp_path: Path, kind, template: str, image: str
):
    """Kubernetes image volumes (`volumes[].image.reference`) are pulled by
    the kubelet like any container image, over the node's network and
    outside the pod's egress policy. The image rules walked containers only,
    so a volume could mount an unpinned image from any registry."""
    policy = render_policy(tmp_path, template)
    verdict, out = admission(tmp_path, policy, kind(image))
    assert verdict == "refused", out
    assert image in out
    verdict, out = admission(tmp_path, policy, kind(f"{ALLOWED}/weights@{DIGEST}"))
    assert verdict == "admitted", out
    verdict, out = admission(tmp_path, policy, kind(None))
    assert verdict == "admitted", out


def test_signature_verification_reaches_an_image_volume(tmp_path: Path):
    """An image volume in the verified repositories is verified like a
    container image -- and naming the volume path must not drop the
    container images Kyverno extracts by default. Nothing here is signed,
    so an image the rule reaches is refused; one it does not reach passes.
    """
    policy = render_policy(tmp_path, "verify-images")
    unverified_volume = pod(f"{ALLOWED}/weights@{DIGEST}")
    unverified_volume["spec"]["containers"][0]["image"] = (
        f"docker.io/library/x@{DIGEST}"
    )
    verdict, out = admission(tmp_path, policy, unverified_volume)
    assert verdict == "refused", out
    assert f"{ALLOWED}/weights" in out
    verdict, out = admission(tmp_path, policy, pod(None))
    assert verdict == "refused", out
    assert OURS in out


# --- 3065: verification scoped to a list nothing enforces -----------------


def test_verification_alone_reaches_every_image(tmp_path: Path):
    """`verifyImages.imageReferences` defaulted to `allowedImageRepos`, and
    Kyverno skips an image outside `imageReferences`. That is only safe
    while the images-allowed policy refuses everything outside the list; with
    `policies.enabled` off it is not rendered, and an unsigned image from
    any other registry was admitted unverified."""
    policy = render_policy(
        tmp_path,
        "verify-images",
        "security.policies.enabled=false",
        "security.policies.allowDisabled=true",
    )
    foreign = pod(None)
    foreign["spec"]["containers"][0]["image"] = FOREIGN
    verdict, out = admission(tmp_path, policy, foreign)
    assert verdict == "refused", out
    assert FOREIGN in out


# --- 3066: the label the delete rule trusts is one the deleter can write ----


@pytest.fixture
def rbac_scope(tmp_path: Path) -> Path:
    return render_policy(tmp_path, "rbac-scope", "apply.rbac.enabled=true")


def test_the_apply_identity_cannot_adopt_a_foreign_object(
    tmp_path: Path, rbac_scope: Path
):
    """The delete rule reads the object's `managed-by` label, and the same
    identity may patch every Job and ConfigMap in the namespace. Relabel,
    then delete: the first step is where it has to stop."""
    foreign = configmap("team-settings", data={"a": "b"})
    relabelled = configmap("team-settings", CONVERTER, data={"a": "b"})
    verdict, out = admission(
        tmp_path, rbac_scope, relabelled, user=APPLY_SA, operation="UPDATE", old=foreign
    )
    assert verdict == "refused", out


def test_the_apply_identity_cannot_write_an_unlabelled_object(
    tmp_path: Path, rbac_scope: Path
):
    """Creating one is not a way round it either: an object the apply
    identity writes is one the converter rendered, and carries its label."""
    verdict, out = admission(
        tmp_path, rbac_scope, configmap("team-settings"), user=APPLY_SA
    )
    assert verdict == "refused", out
    ours = configmap("campaign-kyrk", CONVERTER)
    verdict, out = admission(
        tmp_path,
        rbac_scope,
        configmap("campaign-kyrk", {}),
        user=APPLY_SA,
        operation="UPDATE",
        old=ours,
    )
    assert verdict == "refused", out


def test_the_apply_identity_still_writes_what_it_rendered(
    tmp_path: Path, rbac_scope: Path
):
    ours = configmap("campaign-kyrk", CONVERTER, data={"volumes.txt": "R1\n"})
    verdict, out = admission(tmp_path, rbac_scope, ours, user=APPLY_SA)
    assert verdict == "admitted", out
    verdict, out = admission(
        tmp_path, rbac_scope, ours, user=APPLY_SA, operation="UPDATE", old=ours
    )
    assert verdict == "admitted", out


def test_another_identity_is_not_held_to_the_apply_rules(
    tmp_path: Path, rbac_scope: Path
):
    verdict, out = admission(
        tmp_path,
        rbac_scope,
        configmap("team-settings", CONVERTER),
        user="system:serviceaccount:other:deployer",
        operation="UPDATE",
        old=configmap("team-settings"),
    )
    assert verdict == "not matched", out


# --- the Argo CD hook `init` writes (template/argocd/apply.yaml) ------------

HOOK = REPO / "packages/converter/src/htrflow_converter/template/argocd/apply.yaml"
PINNED = "sha256:" + "0" * 64


@pytest.mark.parametrize("template", ["images-allowed", "images-pinned"])
def test_the_hook_job_init_writes_is_admitted(tmp_path: Path, template: str):
    """The hook is a Job in the release namespace like any other, so the
    image rules hold it too. A release pins the template's image by digest;
    a tag in the source tree stands for that digest here."""
    policy = render_policy(
        tmp_path,
        template,
        "publicResultsBase=https://x/",
        "network.enabled=false",
        values="values-prod.yaml",
    )
    hook = yaml.safe_load(HOOK.read_text(encoding="utf-8"))
    hook["metadata"]["namespace"] = NAMESPACE
    spec = hook["spec"]["template"]["spec"]
    for c in spec["initContainers"] + spec["containers"]:
        c["image"] = re.sub(r"(:[^/@]+|@sha256:[0-9a-f]+)$", f"@{PINNED}", c["image"])
    verdict, out = admission(tmp_path, policy, hook)
    assert verdict == "admitted", out


# --- D15: the production allow-list names repositories, not an organisation -

#: What publish.yml pushes, and nothing else.
PUBLISHED = ("htrflow-batch", "htrflow-web", "htrflow-campaigns")


def test_the_production_allow_list_admits_the_release_and_nothing_else(
    tmp_path: Path,
):
    """values-prod.yaml allowed `docker.io/riksarkivet/`, a prefix every
    repository that organisation ever creates matches -- one made next year
    by another team included. The profile names the three the release
    publishes; a sibling repository in the same organisation is refused."""
    policy = render_policy(
        tmp_path,
        "images-allowed",
        "publicResultsBase=https://x/",
        "network.enabled=false",
        values="values-prod.yaml",
    )
    for repo in PUBLISHED:
        admitted = pod(None)
        admitted["spec"]["containers"][0]["image"] = (
            f"docker.io/riksarkivet/{repo}@{DIGEST}"
        )
        verdict, out = admission(tmp_path, policy, admitted)
        assert verdict == "admitted", out
    for sibling in (
        "docker.io/riksarkivet/other",
        "docker.io/riksarkivet/htrflow-batch-x",
    ):
        refused = pod(None)
        refused["spec"]["containers"][0]["image"] = f"{sibling}@{DIGEST}"
        verdict, out = admission(tmp_path, policy, refused)
        assert verdict == "refused", out


# --- D-2 / S-1 / S-6: a Job the converter renders is the only Job shape ---
#
# The apply identity's `create` on Jobs is any pod spec, and converter.yaml
# names Secrets and a PVC the Jobs mount. The job-shape policy holds a Job
# to what the converter renders -- the renders themselves, straight from
# the converter, are the admitted cases.

FIXTURE = REPO / "packages" / "converter" / "tests" / "fixtures" / "good"
HF_SECRET = "htr-hf-token"
GIT_SECRET = "htrflow-campaigns-git"


def converter_jobs(**config) -> tuple[dict, dict]:
    """The campaign Job and the warm-up Job the converter renders for the
    good fixture, in this namespace, with converter.yaml settings changed."""
    from htrflow_converter import render
    from htrflow_converter.parse import load

    campaigns, pipelines, cfg = load(
        FIXTURE / "campaigns", FIXTURE / "pipelines", FIXTURE / "converter.yaml"
    )
    cfg = cfg.model_copy(update={"namespace": NAMESPACE, **config})
    kyrk = next(c for c in campaigns if c.name == "kyrk")
    demo = pipelines["demo-v1"]
    batch = next(
        o for o in render.campaign_objects(kyrk, demo, cfg) if o["kind"] == "Job"
    )
    warmup = next(o for o in render.pipeline_objects(demo, cfg) if o["kind"] == "Job")
    return batch, warmup


@pytest.fixture
def job_shape(tmp_path: Path) -> Path:
    return render_policy(
        tmp_path,
        "job-shape",
        "apply.rbac.enabled=true",
        f"hfToken.existingSecret={HF_SECRET}",
    )


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"hf_token_secret": HF_SECRET},
        {
            "node_selector": {"nvidia.com/gpu.present": "true"},
            "tolerations": [
                {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
            ],
        },
    ],
    ids=["defaults", "hub-token", "scheduling"],
)
@pytest.mark.parametrize("role", ["batch", "warmup"])
def test_what_the_converter_renders_is_admitted(
    tmp_path: Path, job_shape: Path, config: dict, role: str
):
    job = dict(zip(("batch", "warmup"), converter_jobs(**config)))[role]
    verdict, out = admission(tmp_path, job_shape, job, user=APPLY_SA)
    assert verdict == "admitted", out
    verdict, out = admission(
        tmp_path, job_shape, job, user=APPLY_SA, operation="UPDATE", old=job
    )
    assert verdict == "admitted", out
    verdict, out = admission(tmp_path, job_shape, job, user="kubernetes-admin")
    assert verdict == "admitted", out


@pytest.mark.parametrize(
    "config,role,said",
    [
        # S-1: converter.yaml names the Secret, and a warm-up has egress to
        # the internet and runs pickled models.
        ({"hf_token_secret": GIT_SECRET}, "warmup", GIT_SECRET),
        ({"s3_secret": GIT_SECRET}, "batch", GIT_SECRET),
        ({"data_pvc": "team-archive"}, "batch", "team-archive"),
        ({"data_pvc": "team-archive"}, "warmup", "team-archive"),
        ({"tolerations": [{"operator": "Exists"}]}, "batch", "Exists"),
    ],
    ids=["hub-token-git", "s3-git", "pvc-batch", "pvc-warmup", "tolerate-all"],
)
def test_converter_yaml_cannot_hand_a_job_another_secret_or_volume(
    tmp_path: Path, job_shape: Path, config: dict, role: str, said: str
):
    """Whoever applies it: the operator's own kubeconfig as much as the
    apply identity."""
    job = dict(zip(("batch", "warmup"), converter_jobs(**config)))[role]
    for user in (APPLY_SA, "kubernetes-admin"):
        verdict, out = admission(tmp_path, job_shape, job, user=user)
        assert verdict == "refused", out
        assert said in out


def _pod(job: dict) -> dict:
    return job["spec"]["template"]["spec"]


def _main(job: dict) -> dict:
    return _pod(job)["containers"][0]


def _env(name: str, value: str):
    def change(job: dict) -> None:
        env = _main(job)["env"]
        found = [e for e in env if e["name"] == name]
        if found:
            found[0].clear()
            found[0].update({"name": name, "value": value})
        else:
            env.append({"name": name, "value": value})

    return change


def _set_volume(name: str, source: dict):
    def change(job: dict) -> None:
        for v in _pod(job)["volumes"]:
            if v["name"] == name:
                v.clear()
                v.update({"name": name, **source})

    return change


def _add_volume(name: str, source: dict, mount: str):
    def change(job: dict) -> None:
        _pod(job)["volumes"].append({"name": name, **source})
        _main(job)["volumeMounts"].append({"name": name, "mountPath": mount})

    return change


MUTATIONS = {
    # D-2: another identity's token, or any token at all
    "web-identity": (
        "batch",
        lambda j: _pod(j).update(serviceAccountName="htrflow-web"),
    ),
    "token": ("warmup", lambda j: _pod(j).update(automountServiceAccountToken=True)),
    "projected-token": (
        "warmup",
        _add_volume(
            "t",
            {"projected": {"sources": [{"serviceAccountToken": {"path": "t"}}]}},
            "/t",
        ),
    ),
    # D-2 / S-1: the S3 credentials in the pod that reaches the internet
    "warmup-s3-volume": (
        "warmup",
        _add_volume("s3", {"secret": {"secretName": "htr-batch-s3"}}, "/s3"),
    ),
    "warmup-s3-env": (
        "warmup",
        lambda j: _main(j)["env"].append(
            {
                "name": "S3_BUCKET",
                "valueFrom": {"secretKeyRef": {"name": "htr-batch-s3", "key": "k"}},
            }
        ),
    ),
    "env-from-secret": (
        "batch",
        lambda j: _main(j).update(envFrom=[{"secretRef": {"name": GIT_SECRET}}]),
    ),
    "field-ref": (
        "batch",
        lambda j: _main(j)["env"].append(
            {"name": "X", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}}
        ),
    ),
    "writable-cache": (
        "batch",
        lambda j: [
            m.update(readOnly=False)
            for m in _main(j)["volumeMounts"]
            if m["name"] == "data"
        ],
    ),
    "host-path": ("batch", _add_volume("h", {"hostPath": {"path": "/"}}, "/h")),
    # D-2: a pod label is a network role
    "warmup-label-on-batch": (
        "batch",
        lambda j: j["spec"]["template"]["metadata"]["labels"].update(
            app="htrflow-warmup"
        ),
    ),
    "web-label": (
        "batch",
        lambda j: j["spec"]["template"]["metadata"]["labels"].update(app="htrflow-web"),
    ),
    "extra-label": (
        "warmup",
        lambda j: j["spec"]["template"]["metadata"]["labels"].update(role="api"),
    ),
    "warmup-name": ("warmup", lambda j: j["metadata"].update(name="demo-v1")),
    # D-2: the command is the converter's
    "python-c": (
        "warmup",
        lambda j: _main(j).update(command=["python", "-c", "print(1)"]),
    ),
    "args": ("batch", lambda j: _main(j).update(args=["exec python -c 'print(1)'"])),
    "init-command": (
        "batch",
        lambda j: _pod(j)["initContainers"][0].update(command=["/bin/sh", "-c", "id"]),
    ),
    "second-container": (
        "warmup",
        lambda j: _pod(j)["containers"].append({**_main(j), "name": "side"}),
    ),
    "probe": (
        "batch",
        lambda j: _main(j).update(
            livenessProbe={"exec": {"command": ["/bin/sh", "-c", "id"]}}
        ),
    ),
    "post-start": (
        "warmup",
        lambda j: _main(j).update(
            lifecycle={"postStart": {"exec": {"command": ["/bin/sh", "-c", "id"]}}}
        ),
    ),
    # S-6: the pipeline the Job runs is the ConfigMap the policy read
    "pipeline-path": ("batch", _env("PIPELINE_PATH", "/work/pipeline.yaml")),
    "pipeline-emptydir": ("warmup", _set_volume("pipeline", {"emptyDir": {}})),
    "pipeline-other-configmap": (
        "batch",
        _set_volume("pipeline", {"configMap": {"name": "team-settings"}}),
    ),
    "pipeline-items": (
        "warmup",
        _set_volume(
            "pipeline",
            {
                "configMap": {
                    "name": "htr-pipeline-demo-v1",
                    "items": [{"key": "other", "path": "pipeline.yaml"}],
                }
            },
        ),
    ),
    "pipeline-shadowed": (
        "batch",
        _add_volume("shadow", {"emptyDir": {}}, "/config/pipeline.yaml"),
    ),
}


#: The sentence each mutation is refused with -- a refusal for some other
#: reason would pass a bare verdict check and prove nothing.
SAID = {
    "web-identity": "serviceAccountName is htrflow-web",
    "token": "automountServiceAccountToken is not false",
    "projected-token": "volumes other than configMap",
    "warmup-s3-volume": "Secrets this pod may not read: htr-batch-s3",
    "warmup-s3-env": "Secrets this pod may not read: htr-batch-s3",
    "env-from-secret": "envFrom",
    "field-ref": "env from something other than a Secret key: X",
    "writable-cache": "the model cache mounted writable",
    "host-path": "volumes other than configMap",
    "warmup-label-on-batch": "a warm-up Job is named htr-warmup-",
    "web-label": 'pod label app is "htrflow-web"',
    "extra-label": "pod labels beyond app: role",
    "warmup-name": "a warm-up Job is named htr-warmup-",
    "python-c": "the command is",
    "args": "the script is not the one the converter renders",
    "init-command": "the init container is not the warm-up gate",
    "second-container": "containers are",
    "probe": "a probe or a hook is a command of its own",
    "post-start": "a probe or a hook is a command of its own",
    "pipeline-path": "PIPELINE_PATH is not /config/pipeline.yaml",
    "pipeline-emptydir": "the pipeline volume is not an htr-pipeline-* ConfigMap",
    "pipeline-other-configmap": "ConfigMaps this pod may not mount: team-settings",
    "pipeline-items": "remapping its keys with items",
    "pipeline-shadowed": "the pipeline volume is not the one mount at /config",
}


@pytest.mark.parametrize("case", list(MUTATIONS))
def test_a_job_that_is_not_the_converters_shape_is_refused(
    tmp_path: Path, job_shape: Path, case: str
):
    role, change = MUTATIONS[case]
    job = dict(zip(("batch", "warmup"), converter_jobs()))[role]
    change(job)
    verdict, out = admission(tmp_path, job_shape, job, user=APPLY_SA)
    assert verdict == "refused", out
    assert SAID[case] in out


def test_a_job_claiming_the_warmup_network_role_is_held_to_it_whoever_asks(
    tmp_path: Path, job_shape: Path
):
    """`app: htrflow-warmup` is what the warm-up NetworkPolicy lets out to
    the internet. A Job carrying it is held to the warm-up's shape even
    without the converter's label, from any identity."""
    _, warmup = converter_jobs()
    del warmup["metadata"]["labels"]["htrflow.riksarkivet.se/managed-by"]
    _main(warmup)["command"] = ["python", "-c", "print(1)"]
    verdict, out = admission(tmp_path, job_shape, warmup, user="kubernetes-admin")
    assert verdict == "refused", out


def test_other_jobs_in_the_namespace_are_not_this_policys(
    tmp_path: Path, job_shape: Path
):
    other = job(None)
    other["spec"]["template"]["metadata"] = {"labels": {"app": "rustfs-init"}}
    verdict, out = admission(tmp_path, job_shape, other, user="kubernetes-admin")
    assert verdict == "not matched", out
    verdict, out = admission(tmp_path, job_shape, other, user=APPLY_SA)
    assert verdict == "refused", out


def test_what_kueue_adds_on_admission_is_not_refused(tmp_path: Path, job_shape: Path):
    """Kueue starts a Job by updating it -- under its own identity -- and
    merges its podset's labels and scheduling into the template. The shape
    is fixed at creation (a Job's pod template is immutable once it runs),
    so another identity's update is not re-checked, and the apply
    identity's next server-side apply carries Kueue's fields through."""
    batch, _ = converter_jobs()
    started = yaml.safe_load(yaml.safe_dump(batch))
    started["spec"]["template"]["metadata"]["labels"]["kueue.x-k8s.io/podset"] = "main"
    _pod(started)["nodeSelector"] = {"topology.kubernetes.io/zone": "a"}
    kueue = "system:serviceaccount:kueue-system:kueue-controller-manager"
    verdict, out = admission(
        tmp_path, job_shape, started, user=kueue, operation="UPDATE", old=batch
    )
    assert verdict == "not matched", out
    verdict, out = admission(
        tmp_path, job_shape, started, user=APPLY_SA, operation="UPDATE", old=started
    )
    assert verdict == "admitted", out
    # The apply identity's second field manager hands `spec.suspend` to
    # Kueue on an unpause: a patch of that one field, admitted as is.
    resumed = yaml.safe_load(yaml.safe_dump(started))
    resumed["spec"]["suspend"] = False
    started["spec"]["suspend"] = True
    verdict, out = admission(
        tmp_path, job_shape, resumed, user=APPLY_SA, operation="UPDATE", old=started
    )
    assert verdict == "admitted", out


def test_the_job_image_can_be_held_to_the_wrapper_repository(tmp_path: Path):
    """images-allowed admits every repository the release publishes, the
    web and converter images included. `security.jobImageRepos` narrows a
    campaign or warm-up Job to the wrapper's."""
    policy = render_policy(
        tmp_path,
        "job-shape",
        "apply.rbac.enabled=true",
        "security.jobImageRepos={ghcr.io/riksarkivet/htrflow-batch}",
    )
    batch, warmup = converter_jobs()
    image = "ghcr.io/riksarkivet/htrflow-batch@" + DIGEST
    for j in (batch, warmup):
        for c in [*_pod(j)["containers"], *_pod(j).get("initContainers", [])]:
            c["image"] = image
        verdict, out = admission(tmp_path, policy, j, user=APPLY_SA)
        assert verdict == "admitted", out
        _main(j)["image"] = "ghcr.io/riksarkivet/htrflow-web@" + DIGEST
        verdict, out = admission(tmp_path, policy, j, user=APPLY_SA)
        assert verdict == "refused", out
        assert "htrflow-web" in out


# --- D-2: the pause sync patches spec.active of a converter Job's Workload -

WORKLOAD = f"{NAMESPACE}/Workload/job-kyrk-1a2b3"


def workload(active: bool = True, owner: str | None = "kyrk") -> dict:
    meta: dict = {
        "name": "job-kyrk-1a2b3",
        "namespace": NAMESPACE,
        "labels": {"kueue.x-k8s.io/job-uid": "u-1"},
    }
    if owner is not None:
        meta["ownerReferences"] = [
            {"apiVersion": "batch/v1", "kind": "Job", "name": owner, "uid": "u-1"}
        ]
    return {
        "apiVersion": "kueue.x-k8s.io/v1beta2",
        "kind": "Workload",
        "metadata": meta,
        "spec": {"active": active, "queueName": "htr-batch", "priority": 0},
    }


def owner_job(name: str, labels: dict) -> dict:
    """The Job the admission-time lookup finds."""
    found = job(None)
    found["metadata"] = {"name": name, "namespace": NAMESPACE, "labels": labels}
    return found


def _workload_verdict(verdict: str, out: str) -> str:
    """The Workload's own verdict: its line, not the owner Job's, which the
    CLI checks too."""
    if verdict == "refused" and f"resource {WORKLOAD} failed" not in out:
        return "admitted"
    return verdict


@pytest.mark.parametrize("active", [False, True])
def test_the_apply_identity_pauses_and_resumes_a_converter_workload(
    tmp_path: Path, rbac_scope: Path, active: bool
):
    verdict, out = admission(
        tmp_path,
        rbac_scope,
        workload(active=active),
        user=APPLY_SA,
        operation="UPDATE",
        old=workload(active=not active),
        cluster=[owner_job("kyrk", CONVERTER)],
    )
    assert verdict == "admitted", out


def test_the_apply_identity_changes_nothing_but_spec_active(
    tmp_path: Path, rbac_scope: Path
):
    moved = workload()
    moved["spec"]["queueName"] = "elsewhere"
    verdict, out = admission(
        tmp_path,
        rbac_scope,
        moved,
        user=APPLY_SA,
        operation="UPDATE",
        old=workload(),
        cluster=[owner_job("kyrk", CONVERTER)],
    )
    assert _workload_verdict(verdict, out) == "refused", out


@pytest.mark.parametrize(
    "owner,cluster",
    [
        ("team-job", [owner_job("team-job", {"team": "other"})]),
        ("gone", [owner_job("kyrk", CONVERTER)]),
        (None, [owner_job("kyrk", CONVERTER)]),
    ],
    ids=["foreign-job", "job-gone", "no-job"],
)
def test_the_apply_identity_cannot_pause_a_workload_it_did_not_render(
    tmp_path: Path, rbac_scope: Path, owner: str | None, cluster: list
):
    verdict, out = admission(
        tmp_path,
        rbac_scope,
        workload(active=False, owner=owner),
        user=APPLY_SA,
        operation="UPDATE",
        old=workload(active=True, owner=owner),
        cluster=cluster,
    )
    assert _workload_verdict(verdict, out) == "refused", out
    assert "apply-pauses-only-converter-workloads" in out
