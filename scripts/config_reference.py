#!/usr/bin/env python3
"""Generate docs/reference/configuration.md: every setting of the wrapper,
the web front, the converter, the `htrflow-batch` chart and the frontend
build.

Tables from the three pydantic models and the chart's values.yaml, prose
from config_reference.md beside this file (split on its `<!-- TABLES -->`
line). `make config-reference` writes the page; it cannot drift, because
test_chart_agreement.py fails when the committed page is not what it prints.

The "Set by" column is read off what actually sets each value -- the Job
skeleton and the job-shape policy's env lists, the chart's web Deployment,
the images' `ENV`, the compose stack -- so a setting nothing in a
deployment can reach says so, and test_chart_agreement.py holds every
"set by X" claim to a render in which X changes it.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from htrflow_batch.config import Config as WrapperConfig
from htrflow_converter.models import Campaign, ConverterConfig, Pipeline
from htrflow_web.kube import Config as WebConfig
from pydantic import BaseModel

ROOT = Path(__file__).parents[1]
PAGE = ROOT / "docs" / "reference" / "configuration.md"
VALUES = ROOT / "charts" / "htrflow-batch" / "values.yaml"
JOB_SHAPE = (
    ROOT / "charts" / "htrflow-batch" / "templates" / "policies" / "job-shape.yaml"
)
WEB_TEMPLATE = ROOT / "charts" / "htrflow-batch" / "templates" / "web.yaml"
SKELETON = ROOT / "packages/converter/src/htrflow_converter/manifests/campaign-job.yaml"
DOCKER = ROOT / ".docker"
FRONTEND_CONFIG = ROOT / "frontend" / "src" / "lib" / "config.ts"

#: (ConverterConfig field, chart values path) — one cluster object under two
#: names. test_chart_agreement.py imports this and asserts both sides agree.
AGREEMENTS = [
    ("queue", "queue.name"),
    ("s3_secret", "s3.existingSecret"),
    ("data_pvc", "modelCache.name"),
    ("hf_token_secret", "hfToken.existingSecret"),
]
#: (ConverterConfig list field, chart list path, the key of each entry):
#: the names on both sides must be the same list, in order.
LIST_AGREEMENTS = [("priority_classes", "queue.priorityClasses", "name")]
PAIRS = {("converter", f): f"chart `{p}`" for f, p in AGREEMENTS}
PAIRS |= {("chart", p): f"converter `{f}`" for f, p in AGREEMENTS}
PAIRS |= {("converter", f): f"chart `{p}[].{k}`" for f, p, k in LIST_AGREEMENTS}
PAIRS |= {("chart", p): f"converter `{f}`" for f, p, k in LIST_AGREEMENTS}

#: The results base: one value, four names, three consumers.
RESULTS_BASE = {
    "chart": "publicResultsBase",
    "converter": "public_results_base",
    "web": "HTRFLOW_PUBLIC_RESULTS_BASE",
    "wrapper": "PUBLIC_RESULTS_BASE",
}

#: What a key exposes — who enforces it. Unnamed keys get the honest default.
NONE, PUBLIC = "no secret — nobody", "the public-read results base — nobody"
SECURITY = {
    "S3_BUCKET": "from the S3 Secret (`secretKeyRef`) — cluster",
    "S3_ENDPOINT": "from the S3 Secret (`secretKeyRef`) — cluster",
    "s3_secret": "names the Secret mounted at `/secrets/s3`; job-shape admits "
    "only `s3.existingSecret` — cluster",
    "hf_token_secret": "names the Secret the warm-up reads `HF_TOKEN` from; job-shape "
    "admits only `hfToken.existingSecret` — cluster",
    "data_pvc": "the model-cache PVC; job-shape admits only `modelCache.name` "
    "— cluster",
    "s3.existingSecret": "names that Secret; no template creates it — nobody",
    "hfToken.existingSecret": "the one Secret a warm-up may read (job-shape) — cluster",
    "security.jobImageRepos": "what a campaign or warm-up Job may run — cluster",
    "publicResultsBase": "the public-read results base; `required` — render",
    "web.image": "digest-pinned unless `security.allowTagImages` — render",
    "security.allowTagImages": "opens that digest gate — render",
    "security.psaEnforce": "Pod Security Admission label — cluster",
    "security.policies.allowDisabled": "no admission policy at all — render",
    "network.web.ingressCidrs": "the read API's only gate on a NodePort — cluster",
    "network.web.ingressFrom": "who may reach the read API behind an ingress; the "
    "controller's allow-list keeps browsers out — cluster",
}

#: What `WebConfig.from_env` (or `app.py`) actually falls back to, for a
#: field whose class-level default is only what an unset field parses to,
#: never what a deployed pod runs with. Kept next to the model it annotates,
#: like SECURITY, rather than as a Field kwarg: it costs nothing against
#: packages/web's LOC budget here.
WEB_DEFAULT_DOC = {
    "HTRFLOW_PUBLIC_RESULTS_BASE": "required unless `HTRFLOW_WEB_SITE_ONLY`",
    "HTRFLOW_INTERNAL_RESULTS_BASE": "`HTRFLOW_PUBLIC_RESULTS_BASE`",
    "HTRFLOW_NAMESPACES": "the pod's own namespace, else `htr-batch`",
    "HTRFLOW_WEB_STATIC": "`/app/static`",
}

_ENV = "an environment variable of the container"
SURFACES = [
    ("wrapper", "the batch Job's container", _ENV, WrapperConfig, None),
    ("web", "the read API and campaign browser", _ENV, WebConfig, WEB_DEFAULT_DOC),
    (
        "converter",
        "a campaigns repo",
        "a key of `converter.yaml`",
        ConverterConfig,
        None,
    ),
    ("chart", "`charts/htrflow-batch`", "a key of its `values.yaml`", None, None),
]

#: Who renders each env var the job-shape policy leaves `free` (render.py's
#: `dynamic_env`): a key of converter.yaml or of the pipeline file, or the
#: converter itself. A free var missing here fails the page's render.
FREE_ENV = {
    "PIPELINE_ID": ("pipeline", "id"),
    "IMAGE_DIGEST": ("pipeline", "image"),
    "S3_PREFIX": ("converter", "namespace"),
    "PUBLIC_RESULTS_BASE": ("converter", "public_results_base"),
    "MANIFEST_MAX_BYTES": ("converter", "manifest_max_bytes"),
    "FETCH_MAX_BYTES": ("converter", "fetch_max_bytes"),
    "BACKOFF_LIMIT_PER_INDEX": ("fixed", "the Job's `backoffLimitPerIndex`"),
}
_FREE_SHOWN = {
    "pipeline": "the pipeline file's `{}`",
    "converter": "`converter.yaml` `{}`",
    "fixed": "the converter, fixed: {}",
}
#: What an image's own `ENV` holds, where it comes from at build time.
IMAGE_ENV_DOC = {
    "HTRFLOW_BASE_REVISION": "the htrflow revision the image was built from",
    "HTRFLOW_BATCH_VERSION": "the tag the image is published under",
    "HTRFLOW_WEB_STATIC": "where the image puts the site",
}
LOCAL_ONLY = "**a local run only**"
_OVERRIDDEN = {
    Pipeline: "a pipeline's own `{}:` overrides it",
    Campaign: "a campaign's own `{}:` may lower it",
}


def job_shape() -> dict:
    """The job-shape policy's copy of the skeletons' env and mounts."""
    policy = JOB_SHAPE.read_text(encoding="utf-8")
    return yaml.safe_load(re.search(r"\{\{- \$spec := `([^`]*)`", policy).group(1))


def script_exports() -> list[str]:
    """What the campaign Job's shell prologue exports, per index."""
    job = yaml.safe_load(SKELETON.read_text(encoding="utf-8"))
    script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    return re.findall(r"export (\w+)=", script)


def image_env(dockerfile: str) -> set[str]:
    """The names an image's dockerfile sets with `ENV`."""
    text = (DOCKER / dockerfile).read_text(encoding="utf-8").replace("\\\n", " ")
    lines = re.findall(r"^ENV (.*)$", text, re.M)
    return {name for line in lines for name in re.findall(r"(\w+)=", line)}


def compose_env(service: str) -> set[str]:
    compose = yaml.safe_load((DOCKER / "docker-compose.yml").read_text("utf-8"))
    return set(compose["services"][service].get("environment") or {})


def chart_web_env() -> dict[str, list[str]]:
    """Each HTRFLOW_ env var the chart's web Deployment sets, with the values
    that set it -- empty for one it takes from the downward API."""
    text = WEB_TEMPLATE.read_text(encoding="utf-8")
    found = re.findall(r"- name: (HTRFLOW_\w+)\n\s+(value: [^\n]*|valueFrom:)", text)
    return {n: re.findall(r"\.Values\.([\w.]+)", how) for n, how in found}


def _image(name: str) -> str:
    return f"the image build (`ENV`): {IMAGE_ENV_DOC[name]}"


def _local(name: str, service: str) -> str:
    compose = " (the compose stack sets it)" if name in compose_env(service) else ""
    return f"{LOCAL_ONLY}{compose}"


def wrapper_set_by(name: str) -> str:
    batch = job_shape()["batch"]
    if name in script_exports():
        return "the campaign file: its volume list, one entry per index"
    if name in batch["pinned"]:
        return f"the converter, fixed: `{batch['pinned'][name]}`"
    if name in batch["free"]:
        kind, what = FREE_ENV[name]
        return _FREE_SHOWN[kind].format(what)
    if name in batch["secretEnv"]:
        return f"the S3 Secret (`s3_secret`), its `{name}` key"
    if name in batch["fieldEnv"]:
        return "the Job controller, per attempt (downward API)"
    if name in image_env("htrflow-batch.dockerfile"):
        return _image(name)
    return _local(name, "wrapper") + (
        ": no converter key renders it, and job-shape refuses a Job that sets it"
    )


def web_set_by(name: str) -> str:
    chart = chart_web_env()
    if name in chart and chart[name]:
        return "the chart: " + ", else ".join(f"`{v}`" for v in chart[name])
    if name in chart:
        return "the chart, fixed: the release namespace (no value sets it)"
    if name in image_env("htrflow-web.dockerfile"):
        return _image(name)
    return _local(name, "web") + "; no chart value"


def overrides() -> list[tuple[type[BaseModel], str]]:
    """Campaign and pipeline keys named like a converter.yaml key."""
    fields = ConverterConfig.model_fields
    return [(m, f) for m in _OVERRIDDEN for f in m.model_fields if f in fields]


def converter_set_by(name: str) -> str:
    said = [_OVERRIDDEN[m].format(f) for m, f in overrides() if f == name]
    return "; ".join(["`converter.yaml`", *said])


SET_BY = {
    "wrapper": wrapper_set_by,
    "web": web_set_by,
    "converter": converter_set_by,
    "chart": lambda _: "`values.yaml`",
}

#: The frontend's build-time settings, read by `bun run build` (Vite) and
#: baked into the bundle. Keyed by name; a VITE_ variable config.ts reads
#: that is missing here fails the page's render.
FRONTEND_DOC = {
    "VITE_API_BASE": "the read API's base; `/config.js` overrides it at run time",
    "VITE_RESULTS_BASE": "the results base; `/config.js` overrides it at run time",
    "VITE_RELOAD_MS": "how often the campaign list is fetched again, in ms",
    "VITE_LIVE_MS": "how often a live run log is fetched again, in ms",
}


def frontend_rows() -> list[tuple[str, str]]:
    """(name, default) of each VITE_ variable config.ts reads."""
    text = FRONTEND_CONFIG.read_text(encoding="utf-8")
    found = re.findall(r"env\.(VITE_\w+)(?:\s*\?\?\s*\"([^\"]*)\"|, ([\d_]+)\))", text)
    return [
        (n, _show(s if s or not i else int(i.replace("_", "")))) for n, s, i in found
    ]


#: Env the wrapper reads outside `Config` — not campaign settings, so no
#: `Field` belongs to them. `test_no_setting_may_carry_a_secret` scans the
#: package source for these reads too. Kept here, not just as prose, so the
#: page and the test cannot silently drift from what the source actually
#: reads.
_WARMUP = "the warm-up entrypoint's own contract"
ALSO_READ = [
    ("TERMINATION_LOG_PATH", "main.py", "the path Kubernetes sets, not chosen here"),
    ("HF_HUB_OFFLINE", "warmup.py", _WARMUP),
    ("HF_TOKEN", "warmup.py", "from `hf_token_secret`; read only to log it is set"),
    ("HF_HOME", "warmup.py", _WARMUP),
    ("PIPELINE_ID", "warmup.py", _WARMUP),
    ("PIPELINE_PATH", "warmup.py", _WARMUP),
]


def _show(value: Any) -> str:
    if value in ("", None, [], {}, ()):
        return "*(empty)*"
    dump = yaml.safe_dump(value, default_flow_style=True, width=200)  # as YAML
    text = dump.strip().removesuffix("...").strip()
    return f"`{text if len(text) <= 60 else text[:59] + '…'}`"


def _agrees(surface: str, key: str) -> str:
    if key == RESULTS_BASE.get(surface):
        return ", ".join(f"{s} `{k}`" for s, k in RESULTS_BASE.items() if s != surface)
    return PAIRS.get((surface, key), "—")


def _security(surface: str, key: str) -> str:
    if key.startswith("security.") and key not in SECURITY:
        return "enforced by a Kyverno ClusterPolicy — cluster"
    return SECURITY.get(key, PUBLIC if key == RESULTS_BASE.get(surface) else NONE)


def _model_rows(
    model: type[BaseModel], doc: dict[str, str] | None = None
) -> list[tuple[str, str]]:
    rows = []
    for name, f in model.model_fields.items():
        alias = f.alias or name
        value = f.get_default(call_default_factory=True)
        default = "**required**" if f.is_required() else _show(value)
        rows.append((alias, (doc or {}).get(alias, default)))
    return rows


def _chart_rows(node: dict, prefix: str = "") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key, value in node.items():
        path = f"{prefix}{key}"
        sub = _chart_rows(value, path + ".") if isinstance(value, dict) else []
        rows.extend(sub or [(path, _show(value))])
    return rows


def render() -> str:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    prose = Path(__file__).with_suffix(".md").read_text(encoding="utf-8")
    head, foot = prose.split("<!-- TABLES -->\n")
    out = [head]
    for surface, where, source, model, doc in SURFACES:
        rows = _model_rows(model, doc) if model else _chart_rows(values)
        out.append(f"\n## {surface} — {where}\n\nEach key is {source}.\n\n")
        out.append("| Key | Set by | Default | Must agree with | Security |\n")
        out.append("|---|---|---|---|---|\n")
        out += [
            f"| `{key}` | {SET_BY[surface](key)} | {default} | "
            f"{_agrees(surface, key)} | {_security(surface, key)} |\n"
            for key, default in rows
        ]
        if surface == "wrapper":
            out.append(
                f"\n`Config` is not the whole wrapper env: these {len(ALSO_READ)}\n"
                "names are read directly, by the warm-up entrypoint or by the Job\n"
                "skeleton, never as a campaign setting.\n\n"
                "### Also read from the environment\n\n"
                "| Key | Read by | Why not `Config` |\n|---|---|---|\n"
            )
            out += [f"| `{k}` | `{f}` | {why} |\n" for k, f, why in ALSO_READ]
    out.append(
        "\n## frontend — the campaign browser's build\n\n"
        "Each key is an environment variable of `bun run build`, baked into\n"
        "the bundle: the published image builds with none set.\n\n"
        "| Key | Default | What |\n|---|---|---|\n"
    )
    out += [f"| `{n}` | {d} | {FRONTEND_DOC[n]} |\n" for n, d in frontend_rows()]
    return "".join(out) + foot


if __name__ == "__main__":
    PAGE.write_text(render(), encoding="utf-8")
    print(f"wrote {PAGE.relative_to(ROOT)}")
