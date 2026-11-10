"""``pipeline add``: a pipeline file put in the cluster with no campaigns
repo, beside a pipeline a repo applied there.

What a pipeline's two objects take from ``converter.yaml`` -- the data
volume, the GPU RuntimeClass, the node placement, the Hub token's Secret --
is the namespace's, and on every warm-up Job ``apply`` made. So the warm-up
of the pipeline named by ``--like`` is copied, and what ``render._warmup_job``
takes from the pipeline itself is set for the new one. The ConfigMap is
rendered as ``apply`` renders it, at that pipeline's size: what a size gives
a pod is in ``converter.yaml``, which is not read here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from . import render
from .models import Pipeline
from .render import WARMUP_PREFIX
from .retry import _as_applied

if TYPE_CHECKING:
    from .cluster import Cluster

_NO_LIKE = (
    "no pipeline {like} in namespace {ns} to copy the settings from: {missing} "
    "is missing — --like names a pipeline applied here"
)
_NOT_MADE = (
    "pipeline {like}'s {what} in namespace {ns} was not made by "
    "htrflow-campaigns, so its settings are not copied"
)
_SIZE = (
    'pipelines/{id}.yaml: size "{size}" is not {like}\'s ({like_size}) — the '
    "size comes from --like, as what a size gives a pod is in converter.yaml; "
    "leave size out, or pass --like a pipeline at that size"
)
_THERE = (
    "pipeline {id} is already in namespace {ns}, with another recipe or only "
    "in part — a pipeline id names one recipe for good, so give the file "
    "another name"
)


def load_file(path: Path) -> Pipeline:
    """The pipeline file at ``path``, validated as ``parse.load`` validates
    one in a repo; the id is the file's stem."""
    from .parse import ValidationError, _parse_pipeline

    problems: list[str] = []
    p = _parse_pipeline(path, {}, problems, [])
    if p is None:
        raise ValidationError(problems)
    return p


def _warmup(like: dict, p: Pipeline) -> dict:
    """``like``'s warm-up Job as an apply sent it, with what
    ``render._warmup_job`` takes from the pipeline set for ``p``."""
    job = _as_applied(like)
    # What an apply's pause sync left on the live Job; render sets none.
    job["spec"].pop("suspend", None)
    job["metadata"]["name"] = f"{WARMUP_PREFIX}{p.id}"
    job["metadata"]["labels"][render._PIPELINE_LABEL] = render.label_value(p.id)
    template = job["spec"]["template"]
    template.setdefault("metadata", {}).setdefault("annotations", {})[
        render._RECIPE_ANNOTATION
    ] = p.recipe_sha256
    pod = template["spec"]
    pod["containers"][0]["image"] = p.image
    for c in pod.get("initContainers", []) + pod["containers"]:
        for e in c.get("env") or []:
            if e.get("name") == "PIPELINE_ID":
                e["value"] = p.id
        for m in c.get("volumeMounts") or []:
            if m.get("name") == "data":
                m["subPath"] = p.cache_dir
    for volume in pod.get("volumes") or []:
        if volume.get("name") == "pipeline":
            volume["configMap"]["name"] = f"htr-pipeline-{p.id}"
    return job


def objects(cluster: Cluster, p: Pipeline, like: str) -> list[dict] | None:
    """``p``'s ConfigMap and warm-up Job, labelled as added; ``None`` when
    both are there already with this recipe. Refused (``ClusterError``) when
    ``like`` is not a pipeline applied here, ``p`` names another size, or
    ``p``'s id is taken."""
    from .cluster import ClusterError, made_here

    ns = cluster.namespace
    pair = [
        ("ConfigMap", f"htr-pipeline-{like}"),
        ("Job", f"{WARMUP_PREFIX}{like}"),
    ]
    like_cm, like_job = (cluster.get(kind, name) for kind, name in pair)
    if like_cm is None or like_job is None:
        missing = [
            f"{k} {n}" for (k, n), o in zip(pair, (like_cm, like_job)) if o is None
        ]
        raise ClusterError(
            _NO_LIKE.format(like=like, ns=ns, missing=" and ".join(missing))
        )
    for what, obj in (("ConfigMap", like_cm), ("warm-up Job", like_job)):
        if not made_here(obj):
            raise ClusterError(_NOT_MADE.format(like=like, what=what, ns=ns))
    size = (like_cm["metadata"].get("annotations") or {}).get(render._SIZE_ANNOTATION)
    if p.size is not None and p.size != size:
        raise ClusterError(
            _SIZE.format(id=p.id, size=p.size, like=like, like_size=size or "none")
        )
    made = [render.pipeline_configmap(p, ns, size), _warmup(like_job, p)]
    for obj in made:
        obj["metadata"]["labels"][render.ADDED_LABEL] = "true"
    live = [cluster.get(o["kind"], o["metadata"]["name"]) for o in made]
    if all(o is None for o in live):
        return made
    there = [o for o in live if o is not None]
    if len(there) < len(made) or render.recipe(there) != render.recipe(made):
        raise ClusterError(_THERE.format(id=p.id, ns=ns))
    return None


def add(cluster: Cluster, made: list[dict]) -> None:
    """The ConfigMap, then the warm-up Job, after a dry run of the Job. A
    ConfigMap left under a Job the API server refused is taken back."""
    import contextlib

    from .cluster import ClusterError

    cm, job = made
    cluster.apply(job, dry_run=True)
    cluster.apply(cm)
    try:
        cluster.apply(job)
    except ClusterError:
        with contextlib.suppress(ClusterError):
            cluster.delete_configmap(cm["metadata"]["name"])
        raise
