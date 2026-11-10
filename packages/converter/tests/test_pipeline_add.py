"""``pipeline add``: a pipeline file put in the cluster with no repo, against
``FakeCluster`` seeded by the real ``apply``."""

import shutil
from pathlib import Path

import pytest
import yaml
from fakes import FakeCluster
from test_retry_build import GOOD, WARMUP, _status
from test_retry_cli import serve

from htrflow_converter import cli, render
from htrflow_converter import cluster as cluster_mod
from htrflow_converter.cluster import KEEPS_ADDED, LEASE, PRUNES_RETRIES
from htrflow_converter.parse import load

V2_IMAGE = "ghcr.io/riksarkivet/htrflow-batch@sha256:" + "b" * 64
V2_CM, V2_WARMUP = "htr-pipeline-demo-v2", "htr-warmup-demo-v2"
ADD = ["pipeline", "add", "--namespace", "htr-test", "--like", "demo-v1"]


@pytest.fixture
def cluster(monkeypatch):
    c = FakeCluster()
    monkeypatch.setattr(cli, "_cluster", c.made)
    monkeypatch.setattr(cluster_mod.time, "sleep", lambda _: None)
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    return c


def _v2(tmp_path: Path, **changes) -> Path:
    """demo-v2.yaml outside any repo: demo-v1 with another image and model."""
    v1 = yaml.safe_load((GOOD / "pipelines" / "demo-v1.yaml").read_text())
    v1["steps"][-1]["settings"]["model_settings"] = {"model": "other/trocr"}
    path = tmp_path / "demo-v2.yaml"
    path.write_text(
        yaml.safe_dump({"image": V2_IMAGE, "steps": v1["steps"], **changes})
    )
    return path


def _repo(tmp_path: Path, name: str, *pipeline_files: Path) -> Path:
    repo = tmp_path / name
    shutil.copytree(GOOD, repo)
    for f in pipeline_files:
        shutil.copy(f, repo / "pipelines" / f.name)
    return repo


def _applied(tmp_path: Path, cluster) -> Path:
    repo = _repo(tmp_path, "repo")
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "out")]) == 0
    _status(cluster, "Job", WARMUP, "Complete")
    return repo


def _rendered(tmp_path: Path, v2: Path) -> list[dict]:
    """demo-v2's pair as ``apply`` renders it from a repo that holds it."""
    repo = _repo(tmp_path, "with-v2", v2)
    _, pipelines, cfg = load(
        repo / "campaigns", repo / "pipelines", repo / "converter.yaml"
    )
    return render.pipeline_objects(pipelines["demo-v2"], cfg)


def test_an_added_pipeline_is_what_apply_renders_with_the_added_label(
    tmp_path, cluster, capsys
):
    _applied(tmp_path, cluster)
    v2 = _v2(tmp_path)
    assert cli.main(ADD + [str(v2)]) == 0
    out = capsys.readouterr().out
    assert f"added: ConfigMap/{V2_CM}" in out and f"added: Job/{V2_WARMUP}" in out
    for want in _rendered(tmp_path, v2):
        want["metadata"]["labels"][render.ADDED_LABEL] = "true"
        live = cluster.find(want["kind"], want["metadata"]["name"])
        live["metadata"].pop("uid")
        live["metadata"].pop("managedFields", None)
        live.get("spec", {}).pop("suspend", None)  # the API server's default
        assert live == want


def test_a_prune_by_this_converter_keeps_an_added_pipeline(tmp_path, cluster):
    repo = _applied(tmp_path, cluster)
    assert cli.main(ADD + [str(_v2(tmp_path))]) == 0
    assert cli.main(["apply", "--prune", str(repo), "--out", str(tmp_path / "o2")]) == 0
    assert cluster.find("ConfigMap", V2_CM) and cluster.find("Job", V2_WARMUP)
    assert KEEPS_ADDED in cluster.lease_marks()


def test_an_apply_of_a_repo_that_holds_the_file_takes_it_over(tmp_path, cluster):
    _applied(tmp_path, cluster)
    v2 = _v2(tmp_path)
    assert cli.main(ADD + [str(v2)]) == 0
    repo = _repo(tmp_path, "adopting", v2)
    assert cli.main(["apply", "--prune", str(repo), "--out", str(tmp_path / "o2")]) == 0
    cm = cluster.find("ConfigMap", V2_CM)
    assert render.ADDED_LABEL not in cm["metadata"]["labels"]


def test_an_old_apply_here_refuses_the_add_unless_vouched_for(
    tmp_path, cluster, capsys
):
    _applied(tmp_path, cluster)
    cluster.leases[LEASE]["metadata"]["annotations"] = {PRUNES_RETRIES: "1"}
    v2 = str(_v2(tmp_path))
    assert cli.main(ADD + [v2]) == 1
    assert "does not know added pipelines" in capsys.readouterr().err
    assert cluster.find("ConfigMap", V2_CM) is None
    assert cli.main(ADD + [v2, "--no-old-apply"]) == 0
    assert "--no-old-apply:" in capsys.readouterr().err
    assert cluster.find("Job", V2_WARMUP) is not None


def test_a_dry_run_adds_nothing(tmp_path, cluster, capsys):
    _applied(tmp_path, cluster)
    assert cli.main(ADD + [str(_v2(tmp_path)), "--dry-run"]) == 0
    assert f"would add: Job/{V2_WARMUP}" in capsys.readouterr().out
    assert cluster.find("ConfigMap", V2_CM) is None


def test_the_size_comes_from_like(tmp_path, cluster, capsys):
    _applied(tmp_path, cluster)
    cluster.find("ConfigMap", "htr-pipeline-demo-v1")["metadata"]["annotations"][
        render._SIZE_ANNOTATION
    ] = "large"
    assert cli.main(ADD + [str(_v2(tmp_path, size="small"))]) == 1
    assert 'size "small" is not demo-v1\'s (large)' in capsys.readouterr().err
    assert cli.main(ADD + [str(_v2(tmp_path))]) == 0
    cm = cluster.find("ConfigMap", V2_CM)
    assert cm["metadata"]["annotations"][render._SIZE_ANNOTATION] == "large"


def test_adding_the_same_file_again_changes_nothing_and_another_recipe_is_refused(
    tmp_path, cluster, capsys
):
    _applied(tmp_path, cluster)
    assert cli.main(ADD + [str(_v2(tmp_path))]) == 0
    capsys.readouterr()
    assert cli.main(ADD + [str(_v2(tmp_path))]) == 0
    assert "already in namespace htr-test" in capsys.readouterr().out
    other = _v2(tmp_path, image="ghcr.io/riksarkivet/htrflow-batch@sha256:" + "c" * 64)
    assert cli.main(ADD + [str(other)]) == 1
    assert "with another recipe" in capsys.readouterr().err


def test_like_must_be_a_pipeline_applied_here(tmp_path, cluster, capsys):
    _applied(tmp_path, cluster)
    argv = ADD[:-1] + ["demo-v9", str(_v2(tmp_path))]
    assert cli.main(argv) == 1
    assert "no pipeline demo-v9 in namespace htr-test" in capsys.readouterr().err


def test_a_file_that_does_not_validate_is_not_added(tmp_path, cluster, capsys):
    _applied(tmp_path, cluster)
    bad = tmp_path / "demo-v2.yaml"
    bad.write_text(yaml.safe_dump({"image": "ghcr.io/x:latest", "steps": []}))
    assert cli.main(ADD + [str(bad)]) == 1
    assert "nothing was added" in capsys.readouterr().out
    assert cluster.find("ConfigMap", V2_CM) is None


def test_a_campaign_retried_onto_an_added_pipeline_runs_there(
    tmp_path, cluster, monkeypatch, capsys
):
    _applied(tmp_path, cluster)
    _status(cluster, "Job", "kyrk", "Failed")
    assert cli.main(ADD + [str(_v2(tmp_path))]) == 0
    _status(cluster, "Job", V2_WARMUP, "Complete")
    serve(monkeypatch, {})
    capsys.readouterr()
    argv = ["retry", "kyrk", "--namespace", "htr-test", "--pipeline", "demo-v2"]
    assert cli.main(argv) == 0
    assert "started: Job/kyrk-retry-1 with 3 volumes" in capsys.readouterr().out
    job = cluster.find("Job", "kyrk-retry-1")
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == V2_IMAGE
    # An apply that keeps retries but not added pipelines would delete the
    # ConfigMap the retry mounts: a retry on an added pipeline is refused.
    cluster.leases[LEASE]["metadata"]["annotations"] = {PRUNES_RETRIES: "1"}
    assert cli.main(argv + ["--dry-run"]) == 1
    assert "does not know added pipelines" in capsys.readouterr().err
