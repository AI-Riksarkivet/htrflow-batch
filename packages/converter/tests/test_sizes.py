"""B105: a pipeline picks a named pod size converter.yaml defines.

A size is what one campaign pod asks for -- GPUs, CPU, memory and the
in-memory ``/work`` -- and, through a flavor, which sort of GPU it runs on.
Kueue has no way for a Job to ask for a flavor by name: it tries the
ClusterQueue's flavors in order and skips any whose node labels the pod's
node selector contradicts (v0.19 ``flavorassigner.checkFlavorForPodSets``),
then writes the chosen flavor's labels into the pod as its node selector at
admission (``podset.FromAssignment``). So a size's flavor is rendered as the
flavor's node labels on the pod, and converter.yaml's ``flavors`` repeats
the chart's ``queue.flavors`` labels the way ``priority_classes`` repeats
its classes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError as PydanticError

from htrflow_converter import render
from htrflow_converter.cli import main
from htrflow_converter.models import ConverterConfig
from htrflow_converter.parse import ValidationError, load

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
GOLDEN = Path(__file__).parent / "golden"

LARGE_LABELS = {"nvidia.com/gpu.product": "NVIDIA-A100-SXM4-80GB"}
SIZES = """
flavors:
  - name: l4
    nodeLabels: {nvidia.com/gpu.product: NVIDIA-L4}
  - name: a100
    nodeLabels: {nvidia.com/gpu.product: NVIDIA-A100-SXM4-80GB}
sizes:
  small: {flavor: l4, gpu: 1, cpu: 4, memory: 16Gi}
  large: {flavor: a100, gpu: 1, cpu: 8, memory: 32Gi, workdir: 4Gi}
"""


def _repo(tmp_path: Path, size: str | None = None, config: str = SIZES) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    (repo / "converter.yaml").write_text((repo / "converter.yaml").read_text() + config)
    if size is not None:
        _size(repo, size)
    return repo


def _size(repo: Path, size: str | None) -> None:
    path = repo / "pipelines" / "demo-v1.yaml"
    doc = yaml.safe_load(path.read_text())
    doc.pop("size", None)
    if size is not None:
        doc["size"] = size
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def _load(repo: Path):
    return load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")


def _kyrk_job(repo: Path) -> dict:
    campaigns, pipelines, cfg = _load(repo)
    kyrk = next(c for c in campaigns if c.name == "kyrk")
    objects = render.campaign_objects(kyrk, pipelines["demo-v1"], cfg)
    return next(o for o in objects if o["kind"] == "Job")


def _pod(job: dict) -> dict:
    return job["spec"]["template"]["spec"]


def _env(job: dict) -> dict[str, str]:
    return {e["name"]: e.get("value") for e in _pod(job)["containers"][0]["env"]}


# --- Klart när -------------------------------------------------------------


def test_a_large_pipeline_asks_for_the_large_size_on_the_large_flavor(tmp_path):
    job = _kyrk_job(_repo(tmp_path, "large"))
    pod = _pod(job)
    want = {"cpu": "8", "memory": "32Gi", "nvidia.com/gpu": "1"}
    # Request and limit alike: the in-memory /work counts against the
    # limit, and a pod whose usage is over its request is the first the
    # kubelet evicts under memory pressure -- with its pages.
    assert pod["containers"][0]["resources"] == {"requests": want, "limits": want}
    assert pod["nodeSelector"] == LARGE_LABELS
    work = next(v for v in pod["volumes"] if v["name"] == "work")
    assert work["emptyDir"] == {"medium": "Memory", "sizeLimit": "4Gi"}


def test_an_unknown_size_is_refused_with_the_sizes_there_are(tmp_path):
    with pytest.raises(ValidationError) as refused:
        _load(_repo(tmp_path, "huge"))
    assert refused.value.problems == [
        'pipelines/demo-v1.yaml: size "huge" is not one of converter.yaml\'s '
        "sizes (small, large) — name one of them, or leave size out for the "
        "default"
    ]


def test_with_no_sizes_at_all_every_size_is_refused(tmp_path):
    with pytest.raises(ValidationError) as refused:
        _load(_repo(tmp_path, "large", config=""))
    (problem,) = refused.value.problems
    assert "is not one of converter.yaml's sizes (none)" in problem


def test_a_pipeline_without_a_size_renders_exactly_as_before(tmp_path):
    """Sizes defined or not: the Job of a pipeline that names none is the
    one the converter always rendered, byte for byte."""
    job = _kyrk_job(_repo(tmp_path))
    assert job == yaml.safe_load((GOLDEN / "kyrk.job.yaml").read_text())
    campaigns, pipelines, cfg = _load(_repo(tmp_path / "b"))
    objects = render.pipeline_objects(pipelines["demo-v1"], cfg)
    golden = list(yaml.safe_load_all((GOLDEN / "demo-v1.pipeline.yaml").read_text()))
    assert objects == golden


def test_changing_the_size_of_a_pipeline_a_campaign_runs_is_refused(tmp_path, capsys):
    """The size is part of the recipe a pipeline id names for good."""
    repo = _repo(tmp_path, "small")
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    _size(repo, "large")
    capsys.readouterr()
    assert main(["render", str(repo), "--out", str(out)]) == 1
    assert capsys.readouterr().out.strip() == (
        "pipeline demo-v1 changed (size) but campaigns kyrk, loc still run "
        "it — a pipeline is immutable while campaigns reference it; add a new "
        "pipeline file (demo-v1-2) and point new campaigns at it"
    )
    assert main(["validate", str(repo)]) == 1


def test_giving_a_running_pipeline_a_size_is_a_change_too(tmp_path, capsys):
    repo = _repo(tmp_path)
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    _size(repo, "small")
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 1
    assert "pipeline demo-v1 changed (size)" in capsys.readouterr().out


def test_the_rendered_pipeline_records_its_size(tmp_path):
    """What ran, for anyone reading `rendered/` or the live ConfigMap: the
    size is on the pipeline's own record, beside its steps."""
    _, pipelines, cfg = _load(_repo(tmp_path, "large"))
    objects = render.pipeline_objects(pipelines["demo-v1"], cfg)
    assert render.recipe(objects)["size"] == "large"
    (cm,) = [o for o in objects if o["kind"] == "ConfigMap"]
    assert cm["metadata"]["annotations"]["htrflow.riksarkivet.se/size"] == "large"


# --- the wrapper's lookahead follows the workdir (audit 0923 E-14) ---------


def test_the_lookahead_is_half_the_sizes_workdir(tmp_path):
    """The wrapper's page lookahead lives in /work. A size with a larger
    workdir gets a larger lookahead, a smaller one a smaller -- they were
    two unrelated constants. Half: the rest holds the outputs, HOME and
    TMPDIR."""
    env = _env(_kyrk_job(_repo(tmp_path, "large")))
    assert env["LOOKAHEAD_BYTES"] == str(2 * 1024**3)
    env = _env(_kyrk_job(_repo(tmp_path / "b", "small")))
    assert env["LOOKAHEAD_BYTES"] == str(1024**3)


# --- what converter.yaml may say -----------------------------------------


def _config(text: str) -> ConverterConfig:
    base = {"public_results_base": "https://results.example.org"}
    return ConverterConfig.model_validate({**base, **yaml.safe_load(text)})


#: case -> (converter.yaml text, what the refusal says)
REFUSED = {
    "unknown-flavor": (
        "sizes: {large: {flavor: a100, cpu: 8, memory: 32Gi}}",
        'size "large" names flavor "a100", which converter.yaml\'s '
        "flavors does not list (none)",
    ),
    "memory-under-workdir": (
        "sizes: {large: {cpu: 8, memory: 2Gi}}",
        'size "large" has memory 2Gi, which leaves less than 1Gi beside its '
        "workdir (2Gi)",
    ),
    # review item 6: 1Mi for the process is no margin at all
    "memory-margin": (
        "sizes: {large: {cpu: 8, memory: 2049Mi}}",
        'size "large" has memory 2049Mi, which leaves less than 1Gi',
    ),
    # review item 4: a workdir of bytes rendered LOOKAHEAD_BYTES 0
    "workdir-floor": (
        "sizes: {large: {cpu: 8, memory: 32Gi, workdir: '1'}}",
        "is under 512Mi",
    ),
    "cpu": ("sizes: {large: {cpu: eight, memory: 32Gi}}", "is not a CPU quantity"),
    "memory": ("sizes: {large: {cpu: 8, memory: 32GB}}", "is not a memory quantity"),
    "no-gpu": ("sizes: {large: {cpu: 8, memory: 32Gi, gpu: 0}}", "1 or more"),
    "name": ("sizes: {Large: {cpu: 8, memory: 32Gi}}", "is not a size name"),
    "flavor-twice": (
        "flavors: [{name: a, nodeLabels: {x: '1'}}, {name: a, nodeLabels: {x: '2'}}]",
        'lists flavor "a" twice',
    ),
    # review of PR #35, item 1: Kueue compares only the keys the flavor it
    # tries names, so these let a pod sent to one be admitted on the other
    "flavors-no-shared-key": (
        "flavors: [{name: large-gpu, nodeLabels: {pool: large}}, {name: "
        "small-gpu, nodeLabels: {nvidia.com/gpu.product: NVIDIA-L4}}]",
        'flavors "large-gpu" and "small-gpu" name no label key with different values',
    ),
    "flavors-same-labels": (
        "flavors: [{name: a, nodeLabels: {x: '1'}}, {name: b, nodeLabels: {x: '1'}}]",
        'flavors "a" and "b" name no label key with different values',
    ),
    "flavors-subset": (
        "flavors: [{name: a, nodeLabels: {x: '1'}}, {name: b, nodeLabels: "
        "{x: '1', y: '2'}}]",
        'flavors "a" and "b" name no label key with different values',
    ),
    "no-labels": ("flavors: [{name: a, nodeLabels: {}}]", "has no nodeLabels"),
    "selector-conflict": (
        "node_selector: {nvidia.com/gpu.product: NVIDIA-L4}\n"
        "flavors: [{name: a100, nodeLabels: {nvidia.com/gpu.product: A100}}]\n"
        "sizes: {large: {flavor: a100, cpu: 8, memory: 32Gi}}",
        "node_selector says nvidia.com/gpu.product=NVIDIA-L4",
    ),
}


@pytest.mark.parametrize("case", REFUSED)
def test_converter_yaml_refuses_a_size_no_pod_could_run_as(case: str):
    text, said = REFUSED[case]
    with pytest.raises(PydanticError) as refused:
        _config(text)
    assert said in str(refused.value)


def test_the_example_sizes_parse():
    """The shape the story promises, as an operator writes it."""
    cfg = _config(SIZES)
    assert cfg.sizes["small"].memory == "16Gi"
    assert cfg.sizes["small"].workdir == "2Gi"
    assert [f.name for f in cfg.flavors] == ["l4", "a100"]


def test_memory_in_bytes_is_a_number_as_cpu_is():
    """YAML reads `memory: 17179869184` as a number; cpu took one, memory
    refused it as "must be text" (review item 5)."""
    size = _config("sizes: {big: {cpu: 8, memory: 17179869184}}").sizes["big"]
    assert size.resources()["memory"] == "17179869184"


# --- default_size (review of PR #35, item 2) -------------------------------


def test_a_default_size_is_what_a_pipeline_without_one_runs_at(tmp_path):
    """Without it an unsized pipeline takes the Job skeleton's numbers and
    lands on the first flavor with quota; with it, it runs where the
    operator says light work belongs."""
    repo = _repo(tmp_path, config=SIZES + "default_size: small\n")
    job = _kyrk_job(repo)
    pod = _pod(job)
    want = {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"}
    assert pod["containers"][0]["resources"] == {"requests": want, "limits": want}
    assert pod["nodeSelector"] == {"nvidia.com/gpu.product": "NVIDIA-L4"}
    _, pipelines, cfg = _load(repo)
    objects = render.pipeline_objects(pipelines["demo-v1"], cfg)
    assert render.recipe(objects)["size"] == "small"


def test_a_default_size_that_is_not_a_size_is_refused():
    with pytest.raises(PydanticError) as refused:
        _config(SIZES + "default_size: huge\n")
    assert 'default_size "huge" is not one of the sizes (small, large)' in str(
        refused.value
    )


def test_setting_a_default_size_under_a_running_unsized_pipeline_is_a_change(
    tmp_path, capsys
):
    """Its campaign pods would change size, which a live Job cannot."""
    repo = _repo(tmp_path)
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    config = repo / "converter.yaml"
    config.write_text(config.read_text() + "default_size: small\n")
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 1
    assert "pipeline demo-v1 changed (size)" in capsys.readouterr().out


def test_an_empty_size_is_refused_not_read_as_none(tmp_path):
    with pytest.raises(ValidationError) as refused:
        _load(_repo(tmp_path, ""))
    (problem,) = refused.value.problems
    assert problem.startswith('pipelines/demo-v1.yaml: "size" is empty')
