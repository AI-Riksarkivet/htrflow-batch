"""Building a retry campaign from the original's record and starting it,
against ``FakeCluster``. The cluster is seeded by the real ``apply`` of the
fixture repo, so what a retry reads is exactly what an apply wrote."""

import copy
import json
import shutil
from pathlib import Path

import pytest
from fakes import FakeCluster

from htrflow_converter import cli, render
from htrflow_converter import cluster as cluster_mod
from htrflow_converter.cluster import LEASE, PRUNES_RETRIES, ClusterError
from htrflow_converter.render import (
    _DIGEST_ANNOTATION,
    _PIPELINE_LABEL,
    _SPLIT_STEM,
    CAMPAIGN_LABEL,
    CAMPAIGN_SELECTOR,
    JOB_SPEC_ANNOTATION,
    RETRY_OF_LABEL,
)
from htrflow_converter.retry import (
    load_original,
    next_name,
    refusal,
    retry_objects,
    start,
)

GOOD = Path(__file__).parent / "fixtures" / "good"
WARMUP = "htr-warmup-demo-v1"
PROV = {
    "htrflow.riksarkivet.se/applied-by": "operator",
    "htrflow.riksarkivet.se/applied-at": "2026-11-06T10:00:00Z",
}


@pytest.fixture
def cluster(monkeypatch):
    c = FakeCluster()
    monkeypatch.setattr(cli, "_cluster", c.made)
    monkeypatch.setattr(cluster_mod.time, "sleep", lambda _: None)
    return c


def _status(cluster, kind: str, name: str, condition: str | None) -> None:
    obj = cluster.find(kind, name)
    assert obj is not None
    if condition is None:
        obj.pop("status", None)
    else:
        obj["status"] = {"conditions": [{"type": condition, "status": "True"}]}


def _applied(tmp_path, cluster, *, split: bool = False) -> FakeCluster:
    """The fixture repo applied, its ``kyrk`` campaign over with some
    indexes failed, and the pipeline's warm-up done. ``split`` gives
    ``kyrk`` one volume more than a Job runs, so it renders two parts."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    if split:
        ids = "".join(f"  - R1{i:06d}\n" for i in range(render.MAX_VOLUMES_PER_JOB + 1))
        (repo / "campaigns" / "kyrk.yaml").write_text(
            "pipeline: demo-v1\nvolumes:\n" + ids
        )
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "out")]) == 0
    for job in ("kyrk-part1", "kyrk-part2") if split else ("kyrk",):
        _status(cluster, "Job", job, "Failed")
    _status(cluster, "Job", WARMUP, "Complete")
    return cluster


def _annotations(cluster, name: str) -> dict:
    return cluster.find("ConfigMap", name)["metadata"]["annotations"]


def test_load_original_reads_every_part_and_the_stamped_job(tmp_path, cluster):
    _applied(tmp_path, cluster, split=True)
    o = load_original(cluster, "kyrk")
    assert o.parts == ["kyrk-part1", "kyrk-part2"]
    assert len(o.lines) == render.MAX_VOLUMES_PER_JOB + 1
    assert o.lines[0].startswith("R1000000\t") and o.lines[-1].startswith(
        f"R1{render.MAX_VOLUMES_PER_JOB:06d}\t"
    )
    stamped = _annotations(cluster, "campaign-kyrk-part1")[JOB_SPEC_ANNOTATION]
    assert o.source_job == json.loads(stamped)
    assert o.root == "kyrk-part1", "the name campaign-<name> carries"
    assert o.pipeline == "demo-v1"
    assert o.digest == _annotations(cluster, "campaign-kyrk-part1")[_DIGEST_ANNOTATION]


def test_load_original_of_one_part(tmp_path, cluster):
    _applied(tmp_path, cluster)
    o = load_original(cluster, "kyrk")
    assert o.parts == ["kyrk"] and o.root == "kyrk" and len(o.lines) == 3


def test_load_original_hands_on_only_real_volume_lines(tmp_path, cluster):
    """A blank line is no volume: handed on, it would be an index that runs
    nothing, or a retry of a volume called ''."""
    _applied(tmp_path, cluster)
    cm = cluster.find("ConfigMap", "campaign-kyrk")
    text = cm["data"]["volumes.txt"]
    cm["data"]["volumes.txt"] = "\n" + text.replace("\n", "\n\n", 1) + "\n"
    assert load_original(cluster, "kyrk").lines == text.splitlines()


def test_load_original_falls_back_to_the_live_job_without_a_stamp(tmp_path, cluster):
    _applied(tmp_path, cluster)
    stamped = json.loads(
        _annotations(cluster, "campaign-kyrk").pop(JOB_SPEC_ANNOTATION)
    )
    live = cluster.find("Job", "kyrk")
    # What the API server and the Job controller add to a Job they run.
    live["metadata"].update(resourceVersion="7", creationTimestamp="2026-11-06")
    live["metadata"]["labels"]["batch.kubernetes.io/controller-uid"] = "u"
    live["spec"]["selector"] = {
        "matchLabels": {"batch.kubernetes.io/controller-uid": "u"}
    }
    live["spec"]["manualSelector"] = False
    live["spec"]["template"]["metadata"]["labels"].update({
        "controller-uid": "u", "job-name": "kyrk",
        "batch.kubernetes.io/controller-uid": "u",
        "batch.kubernetes.io/job-name": "kyrk",
    })  # fmt: skip
    o = load_original(cluster, "kyrk")
    job = o.source_job
    tmpl = job["spec"]["template"]["metadata"].get("labels", {})
    assert "selector" not in job["spec"] and "manualSelector" not in job["spec"]
    assert "controller-uid" not in tmpl and "job-name" not in tmpl
    assert not any(k.startswith("batch.kubernetes.io/") for k in tmpl)
    assert not any(
        k.startswith("batch.kubernetes.io/") for k in job["metadata"]["labels"]
    )
    assert set(job) == {"apiVersion", "kind", "metadata", "spec"}
    assert set(job["metadata"]) == {"name", "namespace", "labels", "annotations"}
    assert job["spec"]["template"] == stamped["spec"]["template"]
    assert job["metadata"] == stamped["metadata"]


def test_load_original_refuses_with_neither(tmp_path, cluster):
    _applied(tmp_path, cluster)
    _annotations(cluster, "campaign-kyrk").pop(JOB_SPEC_ANNOTATION)
    cluster.live.remove(cluster.find("Job", "kyrk"))  # the TTL reaped it
    with pytest.raises(
        ClusterError, match="applied before retries existed and its Job is gone"
    ):
        load_original(cluster, "kyrk")


def test_load_original_of_no_campaign(tmp_path, cluster):
    _applied(tmp_path, cluster)
    with pytest.raises(ClusterError, match="no campaign nope in namespace htr-test"):
        load_original(cluster, "nope")


#: What a retry's Job is free to differ from the original's in.
NAME_AND_COUNTS = (
    ("metadata", "name"),
    ("metadata", "labels", CAMPAIGN_LABEL),
    ("metadata", "labels", RETRY_OF_LABEL),
    ("spec", "completions"),
    ("spec", "maxFailedIndexes"),
    ("spec", "parallelism"),
    ("spec", "suspend"),
)


def _campaign_volume(job: dict) -> dict:
    volumes = job["spec"]["template"]["spec"]["volumes"]
    return next(v for v in volumes if v["name"] == "campaign")


def _without(job: dict, paths) -> dict:
    out = copy.deepcopy(job)
    for path in paths:
        node = out
        for key in path[:-1]:
            node = node[key]
        node.pop(path[-1], None)
    _campaign_volume(out)["configMap"].pop("name")
    return out


def test_retry_objects(tmp_path, cluster):
    _applied(tmp_path, cluster)
    o = load_original(cluster, "kyrk")
    lines = ["B\timages:u", "D\timages:u"]
    cm, job = retry_objects(o, "kyrk-retry-1", lines, ["C"], PROV)
    assert cm["kind"] == "ConfigMap" and job["kind"] == "Job"
    assert cm["metadata"]["name"] == "campaign-kyrk-retry-1"
    assert cm["data"] == {
        "volumes.txt": "B\timages:u\nD\timages:u\n",
        "skipped.txt": "C\n",
    }
    assert job["metadata"]["name"] == "kyrk-retry-1"
    assert job["spec"]["completions"] == job["spec"]["maxFailedIndexes"] == 2
    assert job["spec"]["parallelism"] == min(o.source_job["spec"]["parallelism"], 2)
    assert job["metadata"]["labels"][RETRY_OF_LABEL] == o.root
    assert job["metadata"]["labels"][CAMPAIGN_LABEL] == "kyrk-retry-1"
    assert _campaign_volume(job)["configMap"]["name"] == "campaign-kyrk-retry-1"
    assert "suspend" not in job["spec"]
    assert _without(job, NAME_AND_COUNTS) == _without(o.source_job, NAME_AND_COUNTS)
    labels = cm["metadata"]["labels"]
    assert labels[RETRY_OF_LABEL] == "kyrk" and labels[CAMPAIGN_LABEL] == "kyrk-retry-1"
    assert labels[_PIPELINE_LABEL] == "demo-v1"
    assert dict([CAMPAIGN_SELECTOR.split("=")]).items() <= labels.items()
    ann = cm["metadata"]["annotations"]
    assert ann[_DIGEST_ANNOTATION] == o.digest
    assert PROV.items() <= ann.items()
    assert json.loads(ann[JOB_SPEC_ANNOTATION]) == job
    assert o.source_job == load_original(cluster, "kyrk").source_job, "not mutated"


def test_retry_objects_splits_as_render_does(tmp_path, cluster, monkeypatch):
    _applied(tmp_path, cluster)
    monkeypatch.setattr(render, "MAX_VOLUMES_PER_JOB", 2)
    o = load_original(cluster, "kyrk")
    objects = retry_objects(o, "kyrk-retry-1", o.lines, [], PROV)
    assert [(x["kind"], x["metadata"]["name"]) for x in objects] == [
        ("ConfigMap", "campaign-kyrk-retry-1-part1"),
        ("Job", "kyrk-retry-1-part1"),
        ("ConfigMap", "campaign-kyrk-retry-1-part2"),
        ("Job", "kyrk-retry-1-part2"),
    ]
    cm1, job1, cm2, job2 = objects
    assert cm1["data"]["volumes.txt"].splitlines() == o.lines[:2]
    assert cm2["data"]["volumes.txt"].splitlines() == o.lines[2:]
    assert (job1["spec"]["completions"], job2["spec"]["completions"]) == (2, 1)
    assert _campaign_volume(job2)["configMap"]["name"] == "campaign-kyrk-retry-1-part2"
    for x in objects:  # one campaign, as render's parts are
        assert x["metadata"]["labels"][CAMPAIGN_LABEL] == "kyrk-retry-1"


def _seed(cluster, kind: str, name: str, campaign: str, of: str) -> None:
    labels = dict([CAMPAIGN_SELECTOR.split("=")])
    labels.update({CAMPAIGN_LABEL: campaign, RETRY_OF_LABEL: of})
    cluster.live.append({"kind": kind, "metadata": {"name": name, "labels": labels}})


def test_next_name_numbers_on_and_fits_a_dns_label(tmp_path, cluster):
    _applied(tmp_path, cluster)
    assert next_name(cluster, "kyrk") == "kyrk-retry-1"
    _seed(cluster, "ConfigMap", "campaign-kyrk-retry-1", "kyrk-retry-1", "kyrk")
    # A split retry: its ConfigMaps are -partN, its campaign label is not.
    _seed(cluster, "ConfigMap", "campaign-kyrk-retry-3-part1", "kyrk-retry-3", "kyrk")
    _seed(cluster, "ConfigMap", "campaign-loc-retry-9", "loc-retry-9", "loc")
    assert next_name(cluster, "kyrk") == "kyrk-retry-4"
    long = next_name(cluster, "x" * 63)
    assert len(long) <= _SPLIT_STEM and long.endswith("-retry-1")


def test_retrying_a_retry_names_the_root(tmp_path, cluster):
    _applied(tmp_path, cluster)
    o = load_original(cluster, "kyrk")
    objects = retry_objects(o, "kyrk-retry-1", o.lines[1:], [], PROV)
    start(cluster, objects)
    _status(cluster, "Job", "kyrk-retry-1", "Failed")
    again = load_original(cluster, "kyrk-retry-1")
    assert again.root == "kyrk" and again.parts == ["kyrk-retry-1"]
    assert again.lines == o.lines[1:]
    assert again.source_job == objects[1]
    assert next_name(cluster, again.root) == "kyrk-retry-2"


def _volume_held(cluster) -> None:
    cm = cluster.find("ConfigMap", "campaign-loc")  # loc's Job is running
    cm["data"]["volumes.txt"] += "R0001203\tx\n"


def _retry_running(cluster) -> None:
    o = load_original(cluster, "kyrk")
    start(cluster, retry_objects(o, "kyrk-retry-1", o.lines, [], PROV))


def _digest_moved(cluster) -> None:
    pod = cluster.find("Job", WARMUP)["spec"]["template"]["spec"]
    pod["containers"][0]["image"] = "registry.example.org/x@sha256:" + "1" * 64


STATES = {
    "lease-unmarked": lambda c: c.leases[LEASE]["metadata"].update(annotations={}),
    "original-running": lambda c: _status(c, "Job", "kyrk", None),
    "retry-running": _retry_running,
    "warmup-incomplete": lambda c: _status(c, "Job", WARMUP, None),
    "warmup-missing": lambda c: c.live.remove(c.find("Job", WARMUP)),
    "digest-moved": _digest_moved,
    "volume-held": _volume_held,
}


@pytest.mark.parametrize(
    "state, sentence",
    [
        ("lease-unmarked", "the last apply in namespace htr-test ran a converter"),
        ("original-running", "campaign kyrk is still running"),
        ("retry-running", "campaign kyrk-retry-1 is still running"),
        ("warmup-incomplete", f"warm-up Job {WARMUP}"),
        ("warmup-missing", f"warm-up Job {WARMUP}"),
        ("digest-moved", "no longer runs image"),
        ("volume-held", "campaign loc is running volume R0001203"),
    ],
)
def test_refusal(tmp_path, cluster, state, sentence):
    _applied(tmp_path, cluster)
    o = load_original(cluster, "kyrk")
    ids = {"R0001203", "loose-scans"}
    assert refusal(cluster, o, ids) is None
    STATES[state](cluster)
    said = refusal(cluster, o, ids)
    assert said is not None and sentence in said
    assert "\n" not in said


def test_refusal_reads_the_lease_first(tmp_path, cluster):
    """An apply that would prune the retry is the first thing to know: the
    rest is moot if the Job is deleted on the next hook run."""
    _applied(tmp_path, cluster)
    o = load_original(cluster, "kyrk")
    STATES["lease-unmarked"](cluster)
    STATES["original-running"](cluster)
    assert "the last apply" in (refusal(cluster, o, {"R0001203"}) or "")


def test_a_running_campaign_on_another_volume_is_no_refusal(tmp_path, cluster):
    _applied(tmp_path, cluster)  # loc runs R0009999 and R0009998
    o = load_original(cluster, "kyrk")
    assert refusal(cluster, o, {"R0001203"}) is None
    assert PRUNES_RETRIES in cluster.lease_marks()


def test_start_dry_runs_every_job_before_writing_anything(
    tmp_path, cluster, monkeypatch
):
    _applied(tmp_path, cluster)
    monkeypatch.setattr(render, "MAX_VOLUMES_PER_JOB", 2)
    o = load_original(cluster, "kyrk")
    cluster.calls.clear()
    start(cluster, retry_objects(o, "kyrk-retry-1", o.lines, [], PROV))
    sent = [c for c in cluster.calls if c[0] in ("dry-run", "apply")]
    assert sent == [
        ("dry-run", "Job", "kyrk-retry-1-part1"),
        ("dry-run", "Job", "kyrk-retry-1-part2"),
        ("apply", "ConfigMap", "campaign-kyrk-retry-1-part1"),
        ("apply", "Job", "kyrk-retry-1-part1"),
        ("apply", "ConfigMap", "campaign-kyrk-retry-1-part2"),
        ("apply", "Job", "kyrk-retry-1-part2"),
    ]
    assert cluster.find("Job", "kyrk-retry-1-part2") is not None


def test_start_sends_nothing_when_the_job_dry_run_is_refused(
    tmp_path, cluster, monkeypatch
):
    """A ConfigMap written under a Job the API server refuses is a record of
    a retry that never ran -- and the next retry would count it."""
    _applied(tmp_path, cluster)
    monkeypatch.setattr(render, "MAX_VOLUMES_PER_JOB", 2)
    o = load_original(cluster, "kyrk")
    real = FakeCluster._api

    def method(kind, verb, name=""):
        inner = real(cluster, kind, verb, name)
        if verb == "patch" and kind == "Job" and name == "kyrk-retry-1-part2":

            def patch(name, ns, obj, **kw):
                raise cluster_mod.ClusterError("apply Job/kyrk-retry-1-part2: 422")

            return patch
        return inner

    monkeypatch.setattr(cluster, "_api", method)
    cluster.calls.clear()
    with pytest.raises(ClusterError, match="422"):
        start(cluster, retry_objects(o, "kyrk-retry-1", o.lines, [], PROV))
    assert cluster.of("apply") == []
    assert cluster.find("ConfigMap", "campaign-kyrk-retry-1-part1") is None


def test_next_name_steps_past_a_name_another_root_cut_to_the_same(tmp_path, cluster):
    """Two long roots that share their first 42 characters cut to one retry
    name. The first's retry, its Job reaped, leaves its ConfigMap: the
    second's retry is numbered past it, and ``start`` would refuse the name
    anyway rather than take that record over."""
    _applied(tmp_path, cluster)
    first, second = "a" * 45 + "-one", "a" * 45 + "-two"
    taken = next_name(cluster, first)
    assert taken == next_name(cluster, second), "the cut that makes them collide"
    _seed(cluster, "ConfigMap", f"campaign-{taken}", taken, first)
    assert next_name(cluster, second) == next_name(cluster, first)
    assert next_name(cluster, second).endswith("-retry-2")
    o = load_original(cluster, "kyrk")
    cluster.calls.clear()
    with pytest.raises(ClusterError, match=f"ConfigMap campaign-{taken} already"):
        start(cluster, retry_objects(o, taken, o.lines, [], PROV))
    assert [c for c in cluster.calls if c[0] in ("dry-run", "apply")] == []


def test_next_name_steps_past_a_campaign_file_called_like_a_retry(tmp_path, cluster):
    _applied(tmp_path, cluster)
    # A campaign file named kyrk-retry-1.yaml: no retry-of, same name.
    labels = dict([CAMPAIGN_SELECTOR.split("=")]) | {CAMPAIGN_LABEL: "kyrk-retry-1"}
    cluster.live.append(
        {"kind": "Job", "metadata": {"name": "kyrk-retry-1", "labels": labels}}
    )
    assert next_name(cluster, "kyrk") == "kyrk-retry-2"


def test_start_writes_nothing_when_a_later_parts_configmap_exists(
    tmp_path, cluster, monkeypatch
):
    _applied(tmp_path, cluster)
    monkeypatch.setattr(render, "MAX_VOLUMES_PER_JOB", 2)
    cluster.live.append(  # made by hand: no converter label
        {"kind": "ConfigMap", "metadata": {"name": "campaign-kyrk-retry-1-part2"}}
    )
    o = load_original(cluster, "kyrk")
    cluster.calls.clear()
    with pytest.raises(ClusterError, match="campaign-kyrk-retry-1-part2 already"):
        start(cluster, retry_objects(o, "kyrk-retry-1", o.lines, [], PROV))
    assert [c for c in cluster.calls if c[0] in ("dry-run", "apply")] == []
    assert cluster.find("ConfigMap", "campaign-kyrk-retry-1-part1") is None


@pytest.mark.parametrize("gone", ["configmap", "volumes"])
def test_refusal_fails_closed_on_an_unreadable_volume_list(tmp_path, cluster, gone):
    _applied(tmp_path, cluster)  # loc's Job is running on the same pipeline
    o = load_original(cluster, "kyrk")
    cm = cluster.find("ConfigMap", "campaign-loc")
    if gone == "configmap":
        cluster.live.remove(cm)
    else:
        cm["data"].pop("volumes.txt")
    said = refusal(cluster, o, {"R0001203"}) or ""
    assert "campaign loc is running on pipeline demo-v1" in said


@pytest.mark.parametrize("stamp", ["{not json", "[]", '{"kind": "Job"}'])
def test_load_original_refuses_a_corrupt_stamp(tmp_path, cluster, stamp):
    _applied(tmp_path, cluster)
    _annotations(cluster, "campaign-kyrk")[JOB_SPEC_ANNOTATION] = stamp
    with pytest.raises(ClusterError, match="annotation is not a Job"):
        load_original(cluster, "kyrk")


def test_retry_objects_refuses_no_lines(tmp_path, cluster):
    _applied(tmp_path, cluster)
    with pytest.raises(ValueError):
        retry_objects(load_original(cluster, "kyrk"), "kyrk-retry-1", [], [], PROV)
