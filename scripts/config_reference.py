#!/usr/bin/env python3
"""Generate docs/reference/configuration.md: every setting of every surface.

Tables from the three pydantic models and the chart's values.yaml, prose
from config_reference.md beside this file (split on its `<!-- TABLES -->`
line). `make config-reference` writes the page; it cannot drift, because
test_chart_agreement.py fails when the committed page is not what it prints.
"""

from pathlib import Path
from typing import Any

import yaml
from htrflow_batch.config import Config as WrapperConfig
from htrflow_converter.models import ConverterConfig
from htrflow_web.kube import Config as WebConfig
from pydantic import BaseModel

ROOT = Path(__file__).parents[1]
PAGE = ROOT / "docs" / "reference" / "configuration.md"
VALUES = ROOT / "charts" / "htrflow-batch" / "values.yaml"

#: (ConverterConfig field, chart values path) — one cluster object under two
#: names. test_chart_agreement.py imports this and asserts both sides agree.
AGREEMENTS = [
    ("queue", "queue.name"),
    ("s3_secret", "s3.existingSecret"),
    ("data_pvc", "modelCache.name"),
]
PAIRS = {("converter", f): f"chart `{p}`" for f, p in AGREEMENTS}
PAIRS |= {("chart", p): f"converter `{f}`" for f, p in AGREEMENTS}

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
    "s3_secret": "names the Secret mounted at `/secrets/s3` — cluster",
    "s3.existingSecret": "names that Secret; no template creates it — nobody",
    "publicResultsBase": "the public-read results base; `required` — render",
    "web.image": "digest-pinned unless `security.allowTagImages` — render",
    "security.allowTagImages": "opens that digest gate — render",
    "security.psaEnforce": "Pod Security Admission label — cluster",
    "network.web.ingressCidrs": "the only gate on the read API — cluster",
}

SURFACES = [
    ("wrapper", "the batch Job's container", "env", WrapperConfig),
    ("web", "the read API and campaign browser", "env", WebConfig),
    ("converter", "a campaigns repo", "`converter.yaml`", ConverterConfig),
    ("chart", "`charts/htrflow-batch`", "`values.yaml`", None),
]

#: Env the wrapper reads outside `Config` — not campaign settings, so no
#: `Field` belongs to them. `test_no_setting_may_carry_a_secret` scans the
#: package source for these reads too. Kept here, not just as prose, so the
#: page and the test cannot silently drift from what the source actually
#: reads.
_WARMUP = "the warm-up entrypoint's own contract"
ALSO_READ = [
    ("IMAGE_DIGEST", "publish.py", "provenance the Job skeleton stamps"),
    ("TERMINATION_LOG_PATH", "main.py", "the path Kubernetes sets, not chosen here"),
    ("HF_HUB_OFFLINE", "warmup.py", _WARMUP),
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


def _model_rows(model: type[BaseModel]) -> list[tuple[str, str]]:
    rows = []
    for name, f in model.model_fields.items():
        # A field's class-level default can be a value nothing ever runs
        # with -- from_env may fall back to something computed instead.
        # `default_doc` in json_schema_extra, when set, is what to print.
        extra = f.json_schema_extra
        doc = extra.get("default_doc") if isinstance(extra, dict) else None
        if doc:
            shown = doc
        else:
            value = f.get_default(call_default_factory=True)
            shown = "**required**" if f.is_required() else _show(value)
        rows.append((f.alias or name, shown))
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
    for surface, where, source, model in SURFACES:
        rows = _model_rows(model) if model else _chart_rows(values)
        out.append(f"\n## {surface} — {where}\n\n| Key | Source | Default | ")
        out.append("Must agree with | Security |\n|---|---|---|---|---|\n")
        out += [
            f"| `{key}` | {source} | {default} | {_agrees(surface, key)} "
            f"| {_security(surface, key)} |\n"
            for key, default in rows
        ]
        if surface == "wrapper":
            out.append(
                "\n`Config` is not the whole wrapper env: these six names\n"
                "are read directly, by the warm-up entrypoint or by the Job\n"
                "skeleton, never as a campaign setting.\n\n"
                "### Also read from the environment\n\n"
                "| Key | Read by | Why not `Config` |\n|---|---|---|\n"
            )
            out += [f"| `{k}` | `{f}` | {why} |\n" for k, f, why in ALSO_READ]
    return "".join(out) + foot


if __name__ == "__main__":
    PAGE.write_text(render(), encoding="utf-8")
    print(f"wrote {PAGE.relative_to(ROOT)}")
