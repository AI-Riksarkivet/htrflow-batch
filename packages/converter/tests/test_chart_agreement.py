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

import re
import sys
from pathlib import Path

import yaml

from htrflow_converter.models import ConverterConfig

ROOT = Path(__file__).parents[3]
CHART = ROOT / "charts" / "htrflow-batch"
EXAMPLE = ROOT / "examples" / "campaigns" / "converter.yaml"
CONVERTER_SRC = ROOT / "packages" / "converter" / "src" / "htrflow_converter"
JOB_SKELETON = CONVERTER_SRC / "manifests" / "campaign-job.yaml"

# The (converter field, chart values path) table is written once, in the
# generator that also prints it into docs/reference/configuration.md.
sys.path.insert(0, str(ROOT / "scripts"))
from config_reference import (  # noqa: E402
    AGREEMENTS,
    PAGE,
    SECURITY,
    SURFACES,
    _chart_rows,
    _model_rows,
    render,
)


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
    render_src = (CONVERTER_SRC / "render.py").read_text(encoding="utf-8")
    pattern = r'"PUBLIC_RESULTS_BASE"\s*:\s*cfg\.public_results_base'
    assert re.search(pattern, render_src)


def test_security_names_only_keys_the_generator_emits():
    """SECURITY is keyed by hand, next to the models it annotates -- a rename
    on either side must fail here, not just silently drop out of the page's
    Security column."""
    values = _load(CHART / "values.yaml")
    emitted = {
        k
        for _, _, _, m, doc in SURFACES
        for k, _ in (_model_rows(m, doc) if m else _chart_rows(values))
    }
    assert set(SECURITY) <= emitted


def test_the_configuration_page_is_what_the_generator_prints():
    """docs/reference/configuration.md is generated from the three models and
    the chart's values (`make config-reference`). Editing it by hand, or
    changing a default without regenerating, fails here — which is the whole
    reason the page is generated rather than written."""
    assert PAGE.read_text(encoding="utf-8") == render(), (
        f"{PAGE.relative_to(ROOT)} is not what scripts/config_reference.py "
        "prints — run `make config-reference`"
    )
