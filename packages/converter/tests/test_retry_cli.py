"""``htrflow-campaigns retry`` end to end against ``FakeCluster`` and a mock
results proxy. The cluster is seeded by the real ``apply`` of the fixture
repo (see ``test_retry_build``)."""

import copy

import httpx
import pytest
from fakes import FakeCluster
from test_retry_build import WARMUP, _applied, _status

from htrflow_converter import cli
from htrflow_converter import cluster as cluster_mod
from htrflow_converter.cluster import LEASE, PRUNES_RETRIES

LOG_P = (
    "2026-11-01 12:00:02 ERROR permanent failure in manifest: canvas 4 has no "
    "image — a retry changes nothing — fix the campaign or pipeline file\n"
)
OBJECTS = ["kyrk", "campaign-kyrk", "campaign-kyrk-status"]


@pytest.fixture
def cluster(monkeypatch):
    c = FakeCluster()
    monkeypatch.setattr(cli, "_cluster", c.made)
    monkeypatch.setattr(cluster_mod.time, "sleep", lambda _: None)
    monkeypatch.setenv("HTRFLOW_USER", "ann")
    monkeypatch.setenv("HTRFLOW_PASSWORD", "pw")
    return c


def serve(monkeypatch, files, status=None):
    """A results proxy holding ``files`` (path without the leading slash)."""
    seen = []

    def handler(request):
        path = request.url.path
        if path.endswith("/_login"):
            return httpx.Response(204)
        seen.append(path)
        if status is not None:
            return httpx.Response(status, json={"detail": "the proxy says no"})
        body = files.get(path.removeprefix("/results/"))
        return httpx.Response(404) if body is None else httpx.Response(200, text=body)

    real = httpx.Client
    monkeypatch.setattr(
        cli.httpx,
        "Client",
        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k),
    )
    return seen


def four(tmp_path, cluster):
    """kyrk with four volumes: x1 finished, x2 never started, x3 failed for
    good, x4 never started."""
    _applied(tmp_path, cluster)
    cm = cluster.find("ConfigMap", "campaign-kyrk")
    cm["data"]["volumes.txt"] = "".join(f"x{i}\timages:u\n" for i in range(1, 5))
    return {
        "htr-test/demo-v1/x1/manifest.json": "{}",
        "htr-test/status/logs/demo-v1/x3.txt": LOG_P,
    }


def snapshot(cluster, names):
    return copy.deepcopy([o for o in cluster.live if o["metadata"]["name"] in names])


def started(cluster):
    return [
        o["metadata"]["name"] for o in cluster.live if "retry" in o["metadata"]["name"]
    ]


def test_dry_run_prints_the_summary_and_creates_nothing(
    tmp_path, cluster, monkeypatch, capsys
):
    serve(monkeypatch, four(tmp_path, cluster))
    capsys.readouterr()  # the seeding apply's output
    before = copy.deepcopy(cluster.live)
    rc = cli.main(["retry", "kyrk", "--namespace", "htr-test", "--dry-run"])
    assert rc == 0
    assert cluster.live == before
    assert capsys.readouterr().out == (
        "campaign kyrk: 4 volumes, 1 done, 3 not done\n"
        "  2  retry      no run log: the volume never started\n"
        "  1  permanent  manifest: canvas 4 has no image "
        "(skipped; --include-permanent takes it)\n"
        "would start: Job/kyrk-retry-1 with 2 volumes\n"
    )


def test_retry_starts_the_job_and_leaves_the_original_alone(
    tmp_path, cluster, monkeypatch, capsys
):
    serve(monkeypatch, four(tmp_path, cluster))
    before = snapshot(cluster, OBJECTS)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 0
    assert "started: Job/kyrk-retry-1 with 2 volumes" in capsys.readouterr().out
    assert snapshot(cluster, OBJECTS) == before
    cm = cluster.find("ConfigMap", "campaign-kyrk-retry-1")
    assert cm["data"] == {"volumes.txt": "x2\timages:u\nx4\timages:u\n"}
    ann = cm["metadata"]["annotations"]
    assert ann["htrflow.riksarkivet.se/retry-skipped"] == "x3"
    assert ann["htrflow.riksarkivet.se/retry-skipped-count"] == "1"
    assert ann["htrflow.riksarkivet.se/applied-at"].endswith("Z")
    assert "htrflow.riksarkivet.se/applied-by" in ann
    assert not any("commit" in k for k in ann)
    assert cluster.find("Job", "kyrk-retry-1") is not None


def test_include_permanent_takes_them_too(tmp_path, cluster, monkeypatch, capsys):
    serve(monkeypatch, four(tmp_path, cluster))
    rc = cli.main(["retry", "kyrk", "--namespace", "htr-test", "--include-permanent"])
    assert rc == 0
    assert "started: Job/kyrk-retry-1 with 3 volumes" in capsys.readouterr().out
    cm = cluster.find("ConfigMap", "campaign-kyrk-retry-1")
    assert cm["data"]["volumes.txt"].count("\n") == 3
    assert set(cm["data"]) == {"volumes.txt"}
    assert "htrflow.riksarkivet.se/retry-skipped" not in cm["metadata"]["annotations"]


def test_nothing_to_retry_exits_0_and_creates_nothing(
    tmp_path, cluster, monkeypatch, capsys
):
    files = four(tmp_path, cluster)
    files.update({f"htr-test/demo-v1/x{i}/manifest.json": "{}" for i in (2, 3, 4)})
    serve(monkeypatch, files)
    before, logged = copy.deepcopy(cluster.live), len(cluster.lease_log)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("nothing to retry: all 4 volumes of kyrk are done\n")
    assert cluster.live == before and len(cluster.lease_log) == logged


def test_only_permanent_failures_left_is_nothing_to_retry(
    tmp_path, cluster, monkeypatch, capsys
):
    files = four(tmp_path, cluster)
    files.update({f"htr-test/demo-v1/x{i}/manifest.json": "{}" for i in (2, 4)})
    serve(monkeypatch, files)
    before = copy.deepcopy(cluster.live)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 0
    assert "nothing to retry" in capsys.readouterr().out
    assert cluster.live == before


def test_a_refusal_exits_1_with_its_sentence(tmp_path, cluster, monkeypatch, capsys):
    serve(monkeypatch, four(tmp_path, cluster))
    _status(cluster, "Job", WARMUP, None)
    before = copy.deepcopy(cluster.live)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 1
    err = capsys.readouterr().err
    assert f"warm-up Job {WARMUP}" in err and err.count("\n") == 1
    assert cluster.live == before


def test_a_dry_run_is_refused_too(tmp_path, cluster, monkeypatch, capsys):
    serve(monkeypatch, four(tmp_path, cluster))
    _status(cluster, "Job", WARMUP, None)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test", "--dry-run"]) == 1
    assert "would start" not in capsys.readouterr().out


def test_a_results_error_exits_1_before_anything_is_created(
    tmp_path, cluster, monkeypatch, capsys
):
    four(tmp_path, cluster)
    serve(monkeypatch, {}, status=403)
    before, logged = copy.deepcopy(cluster.live), len(cluster.lease_log)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 1
    assert "the proxy says no" in capsys.readouterr().err
    assert cluster.live == before and len(cluster.lease_log) == logged


def test_an_unknown_campaign_exits_1(tmp_path, cluster, monkeypatch, capsys):
    serve(monkeypatch, four(tmp_path, cluster))
    assert cli.main(["retry", "nope", "--namespace", "htr-test"]) == 1
    assert "no campaign nope" in capsys.readouterr().err


def test_the_results_url_comes_from_the_job_or_the_flag(tmp_path, cluster, monkeypatch):
    seen = serve(monkeypatch, four(tmp_path, cluster))
    cli.main(["retry", "kyrk", "--namespace", "htr-test", "--dry-run"])
    assert seen and all(p.startswith("/results/htr-test/") for p in seen)
    seen.clear()
    url = "https://other.example.org/r"
    cli.main(["retry", "kyrk", "--namespace", "htr-test", "--dry-run",
              "--results-url", url])  # fmt: skip
    assert seen and all(p.startswith("/r/htr-test/") for p in seen)


def test_the_password_is_never_an_argument(tmp_path, cluster):
    with pytest.raises(SystemExit):
        cli.main(["retry", "kyrk", "--namespace", "n", "--password", "x"])


def test_retry_passes_the_lease_marks_through(tmp_path, cluster, monkeypatch):
    serve(monkeypatch, four(tmp_path, cluster))
    assert PRUNES_RETRIES in cluster.leases[LEASE]["metadata"]["annotations"]
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 0
    assert PRUNES_RETRIES in cluster.leases[LEASE]["metadata"]["annotations"]


def test_a_refused_retry_leaves_an_unmarked_lease_unmarked(
    tmp_path, cluster, monkeypatch, capsys
):
    serve(monkeypatch, four(tmp_path, cluster))
    cluster.leases[LEASE]["metadata"]["annotations"] = {}
    held = len(cluster.lease_log)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 1
    assert "the last apply in namespace htr-test" in capsys.readouterr().err
    assert cluster.leases[LEASE]["metadata"]["annotations"] == {}
    assert len(cluster.lease_log) == held, "refused before taking the Lease"
    assert started(cluster) == []


def test_the_retry_holds_the_lease_while_it_checks_and_writes(
    tmp_path, cluster, monkeypatch
):
    serve(monkeypatch, four(tmp_path, cluster))
    held = len(cluster.lease_log)
    assert cli.main(["retry", "kyrk", "--namespace", "htr-test"]) == 0
    assert len(cluster.lease_log) == held + 2  # taken, released
