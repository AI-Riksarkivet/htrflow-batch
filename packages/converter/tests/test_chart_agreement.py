"""The converter and charts/htrflow-batch agree by naming convention only.

Neither side can see the other: the chart creates the queue, the S3 Secret
and the model-cache PVC, and the converter renders Jobs that *reference*
them by name. Until this test they agreed because two files said the same
word, and `examples/campaigns/converter.yaml` asked a human to keep it that
way in a comment. A rename on one side now fails here.

Only keys both sides have are checked: `namespace` is the release namespace
(a `helm -n` argument, not a value) and `runtime_class` has no chart key at
all — docs/reference/configuration.md lists what is one-sided.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from htrflow_converter.models import ConverterConfig

ROOT = Path(__file__).parents[3]
CHART = ROOT / "charts" / "htrflow-batch"
EXAMPLE = ROOT / "examples" / "campaigns" / "converter.yaml"
CONVERTER_SRC = ROOT / "packages" / "converter" / "src" / "htrflow_converter"
JOB_SKELETON = CONVERTER_SRC / "manifests" / "campaign-job.yaml"

#: (ConverterConfig field, path in charts/htrflow-batch/values.yaml).
AGREEMENTS = [
    ("queue", "queue.name"),
    ("s3_secret", "s3.existingSecret"),
    ("data_pvc", "modelCache.name"),
]


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _at(values: dict, path: str) -> object:
    for key in path.split("."):
        values = values[key]
    return values


def _disagreements(config: dict) -> list[str]:
    values = _load(CHART / "values.yaml")
    return [
        f"`{field}` is {config[field]!r} but the chart's `{path}` is "
        f"{_at(values, path)!r} — they name one cluster object"
        for field, path in AGREEMENTS
        if config[field] != _at(values, path)
    ]


def test_converter_defaults_agree_with_the_chart_defaults():
    defaults = {f: getattr(ConverterConfig(), f) for f, _ in AGREEMENTS}
    assert _disagreements(defaults) == []


def test_the_example_campaigns_repo_agrees_with_the_chart_defaults():
    assert _disagreements(_load(EXAMPLE)) == []


def test_the_results_base_reaches_both_of_its_consumers():
    """One value under four names: `publicResultsBase` (chart) reaches the web
    front as `HTRFLOW_PUBLIC_RESULTS_BASE` and the wrapper as
    `PUBLIC_RESULTS_BASE`, which the converter fills from
    `converter.yaml`'s `public_results_base`. Renaming any one of the four
    silently strands a consumer, so the chain is asserted end to end."""
    web = (CHART / "templates" / "web.yaml").read_text(encoding="utf-8")
    assert "name: HTRFLOW_PUBLIC_RESULTS_BASE" in web
    assert ".Values.publicResultsBase" in web
    job_env = _load(JOB_SKELETON)["spec"]["template"]["spec"]["containers"][0]["env"]
    assert "PUBLIC_RESULTS_BASE" in [e["name"] for e in job_env]
    render = (CONVERTER_SRC / "render.py").read_text(encoding="utf-8")
    assert '"PUBLIC_RESULTS_BASE": cfg.public_results_base' in render
