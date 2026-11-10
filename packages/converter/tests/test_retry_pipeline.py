"""``retry --pipeline``: a campaign's volumes run again on another pipeline
the repo has applied, against ``FakeCluster`` seeded by the real ``apply``."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fakes import FakeCluster
from test_retry_build import GOOD, WARMUP, _status
from test_retry_cli import serve

from htrflow_converter import cli, render
from htrflow_converter import cluster as cluster_mod
from htrflow_converter.cluster import ClusterError
from htrflow_converter.parse import load
from htrflow_converter.render import (
    _DIGEST_ANNOTATION,
    _PIPELINE_LABEL,
    _PRIORITY_LABEL,
    _SIZE_ANNOTATION,
    JOB_SPEC_ANNOTATION,
    RETRY_OF_LABEL,
)
from htrflow_converter.retry import load_original, on_pipeline, retry_objects

V2_IMAGE = "ghcr.io/riksarkivet/htrflow-batch@sha256:" + "b" * 64
WARMUP_V2 = "htr-warmup-demo-v2"


@pytest.fixture
def cluster(monkeypatch):
    c = FakeCluster()
    monkeypatch.setattr(cli, "_cluster", c.made)
    monkeypatch.setattr(cluster_mod.time, "sleep", lambda _: None)
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    return c


def _repo(tmp_path: Path, *, loc_on_v2: bool = False) -> Path:
    """The fixture repo with a second pipeline, demo-v2: another image and
    another recognition model. ``kyrk`` runs on demo-v1 at priority htr-bulk; ``loc``
    on demo-v2 when ``loc_on_v2``."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    v1 = yaml.safe_load((repo / "pipelines" / "demo-v1.yaml").read_text())
    v1["steps"][-1]["settings"]["model_settings"] = {"model": "other/trocr"}
    v2 = {"image": V2_IMAGE, "steps": v1["steps"]}
    (repo / "pipelines" / "demo-v2.yaml").write_text(yaml.safe_dump(v2))
    kyrk = repo / "campaigns" / "kyrk.yaml"
    kyrk.write_text(kyrk.read_text() + "priority: htr-bulk\n")
    if loc_on_v2:
        loc = repo / "campaigns" / "loc.yaml"
        loc.write_text(loc.read_text().replace("demo-v1", "demo-v2"))
    return repo


def _applied(tmp_path, cluster, **kw) -> Path:
    repo = _repo(tmp_path, **kw)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "out")]) == 0
    _status(cluster, "Job", "kyrk", "Failed")
    _status(cluster, "Job", WARMUP, "Complete")
    _status(cluster, "Job", WARMUP_V2, "Complete")
    return repo


def _rendered(repo: Path, campaign: str, pipeline: str) -> dict:
    """``campaign``'s Job as this converter renders it on ``pipeline``."""
    campaigns, pipelines, cfg = load(
        repo / "campaigns", repo / "pipelines", repo / "converter.yaml"
    )
    c = next(c for c in campaigns if c.name == campaign)
    objects = render.campaign_objects(c, pipelines[pipeline], cfg)
    return next(o for o in objects if o["kind"] == "Job")


def test_a_job_moved_onto_a_pipeline_is_what_render_makes_there(tmp_path, cluster):
    repo = _applied(tmp_path, cluster)
    moved = on_pipeline(cluster, load_original(cluster, "kyrk"), "demo-v2")
    assert moved.source_job == _rendered(repo, "kyrk", "demo-v2")
    assert (moved.pipeline, moved.digest) == ("demo-v2", V2_IMAGE)
    assert moved.moved_from == "demo-v1" and moved.settings_from == ""
    assert moved.record_labels[_PIPELINE_LABEL] == "demo-v2"
    assert moved.lines == load_original(cluster, "kyrk").lines


def test_the_retry_of_a_moved_campaign_is_recorded_on_its_new_pipeline(
    tmp_path, cluster
):
    _applied(tmp_path, cluster)
    moved = on_pipeline(cluster, load_original(cluster, "kyrk"), "demo-v2")
    cm, job = retry_objects(moved, "kyrk-retry-1", moved.lines[:1], [], {})
    assert cm["metadata"]["labels"][_PIPELINE_LABEL] == "demo-v2"
    assert cm["metadata"]["labels"][RETRY_OF_LABEL] == "kyrk"
    assert cm["metadata"]["annotations"][_DIGEST_ANNOTATION] == V2_IMAGE
    assert json.loads(cm["metadata"]["annotations"][JOB_SPEC_ANNOTATION]) == job
    # The retry's own record says demo-v2, so retrying it again stays there.
    cluster.apply(cm)
    again = load_original(cluster, "kyrk-retry-1")
    assert (again.pipeline, again.root) == ("demo-v2", "kyrk")


def test_a_campaign_that_ran_on_the_pipeline_gives_its_settings(tmp_path, cluster):
    repo = _applied(tmp_path, cluster, loc_on_v2=True)
    moved = on_pipeline(cluster, load_original(cluster, "kyrk"), "demo-v2")
    assert moved.settings_from == "loc"
    loc = _rendered(repo, "loc", "demo-v2")
    # loc's window and everything the pipeline decides; kyrk's own priority.
    loc["metadata"]["labels"][_PRIORITY_LABEL] = "htr-bulk"
    # apply's Kueue hold, which ``retry_objects`` drops for every retry.
    moved.source_job["spec"].pop("suspend")
    assert moved.source_job == loc


def test_the_same_pipeline_is_no_move(tmp_path, cluster):
    _applied(tmp_path, cluster)
    original = load_original(cluster, "kyrk")
    assert on_pipeline(cluster, original, "demo-v1") is original


@pytest.mark.parametrize("gone", ["ConfigMap", "Job"])
def test_a_pipeline_not_applied_here_is_refused(tmp_path, cluster, gone):
    _applied(tmp_path, cluster)
    name = WARMUP_V2 if gone == "Job" else "htr-pipeline-demo-v2"
    cluster.live.remove(cluster.find(gone, name))
    with pytest.raises(ClusterError, match="no pipeline demo-v2 in namespace htr-test"):
        on_pipeline(cluster, load_original(cluster, "kyrk"), "demo-v2")


def test_another_size_is_refused_with_no_campaign_on_it_to_copy(tmp_path, cluster):
    _applied(tmp_path, cluster)
    cm = cluster.find("ConfigMap", "htr-pipeline-demo-v2")
    cm["metadata"]["annotations"][_SIZE_ANNOTATION] = "large"
    with pytest.raises(ClusterError, match="runs at size large"):
        on_pipeline(cluster, load_original(cluster, "kyrk"), "demo-v2")


def test_retry_onto_a_pipeline_reads_done_there_and_starts(
    tmp_path, cluster, monkeypatch, capsys
):
    _applied(tmp_path, cluster)
    # Done on demo-v1 means nothing on demo-v2: only demo-v2's results count.
    seen = serve(monkeypatch, {"htr-test/demo-v2/R0001203/manifest.json": "{}"})
    capsys.readouterr()
    argv = ["retry", "kyrk", "--namespace", "htr-test", "--pipeline", "demo-v2"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "campaign kyrk: 3 volumes, 1 done, 2 not done" in out
    assert "pipeline: demo-v1 -> demo-v2" in out
    assert "started: Job/kyrk-retry-1 with 2 volumes" in out
    assert all("/demo-v1/" not in path for path in seen)
    job = cluster.find("Job", "kyrk-retry-1")
    assert job["metadata"]["labels"][_PIPELINE_LABEL] == "demo-v2"
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == V2_IMAGE
    assert job["metadata"]["labels"][_PRIORITY_LABEL] == "htr-bulk"


def test_retry_onto_a_warm_up_still_running_is_refused(
    tmp_path, cluster, monkeypatch, capsys
):
    _applied(tmp_path, cluster)
    _status(cluster, "Job", WARMUP_V2, None)
    serve(monkeypatch, {})
    argv = ["retry", "kyrk", "--namespace", "htr-test", "--pipeline", "demo-v2"]
    assert cli.main(argv + ["--dry-run"]) == 1
    assert "pipeline demo-v2's warm-up Job" in capsys.readouterr().err


def test_a_move_off_a_pipeline_whose_size_is_no_longer_known_is_refused(
    tmp_path, cluster
):
    _applied(tmp_path, cluster)
    original = load_original(cluster, "kyrk")
    cluster.live.remove(cluster.find("ConfigMap", "htr-pipeline-demo-v1"))
    with pytest.raises(ClusterError, match="a size no longer known"):
        on_pipeline(cluster, original, "demo-v2")
