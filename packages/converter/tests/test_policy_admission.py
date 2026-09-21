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
) -> tuple[str, str]:
    """("refused" | "admitted" | "not matched", CLI output) for one request.

    An error is neither verdict -- a policy the CLI cannot evaluate would
    otherwise read as "admitted" -- so it fails the test outright.
    """
    res = tmp_path / "resource.yaml"
    res.write_text(yaml.safe_dump(resource), encoding="utf-8")
    cmd = ["kyverno", "apply", str(policy), "--resource", str(res), "--remove-color"]
    global_values: dict = {"request.operation": operation}
    if old is not None:
        global_values["request.oldObject"] = _replacing(resource, old)
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "cli.kyverno.io/v1alpha1",
                "kind": "Value",
                "metadata": {"name": "request"},
                "globalValues": global_values,
            }
        ),
        encoding="utf-8",
    )
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
    policy = render_policy(tmp_path, "verify-images", "security.policies.enabled=false")
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
