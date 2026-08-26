from pathlib import Path

from htrflow_reconciler.jobspec import ReconcilerConfig
from htrflow_reconciler.main import tick
from htrflow_reconciler.status import JobState, job_name

PIPELINE = """image: r/i@sha256:abc
steps:
  - step: Segmentation
"""
CAMPAIGN = """pipeline: demo-v1
volumes:
  - R0000001
  - R0000002
  - id: loose
    images: [http://x/1.jpg]
"""


class FakeBucket:
    def __init__(self, done=(), stored=None):
        self._done = set(done)
        self.stored = stored or {}
        self.written = {}
        self.put_text_calls = 0

    def done_volumes(self, pipeline_id):
        return {v: "2026-08-25T10:00:00Z" for v in self._done}

    def read_json(self, key):
        return self.stored.get(key) or self.written.get(key)

    def write_json(self, key, obj):
        self.written[key] = obj

    def put_text(self, key, text):
        self.written[key] = text
        self.put_text_calls += 1

    def exists(self, key):
        return key in self.written

    def count_pages(self, pipeline_id, volume_id):
        return 638 if volume_id in self._done else 0


WARMED = JobState(active=False, failed=False, succeeded=True)


class _AllWarmed(dict):
    """Default warm-up snapshot: every pipeline's cache is already filled, so
    the submission tests below exercise submission, not the warm-up gate."""

    def get(self, key, default=None):
        return WARMED


class FakeCluster:
    def __init__(self, jobs=None, warmups=None):
        self._jobs = jobs or {}
        self._warmups = warmups
        self.created, self.deleted, self.configmaps = [], [], {}

    def jobs(self):
        return dict(self._jobs)

    def warmups(self):
        return dict(self._warmups) if self._warmups is not None else _AllWarmed()

    def create_job(self, job):
        self.created.append(job)

    def delete_job(self, name):
        self.deleted.append(name)

    def get_configmap_steps(self, pipeline_id):
        return self.configmaps.get(pipeline_id)

    def ensure_configmap(self, pipeline_id, steps_yaml):
        self.configmaps[pipeline_id] = steps_yaml

    def job_logs(self, name, tail=50):
        return "boom traceback"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "campaigns").mkdir(exist_ok=True)
    (tmp_path / "pipelines").mkdir(exist_ok=True)
    (tmp_path / "campaigns" / "trolldom.yaml").write_text(CAMPAIGN)
    (tmp_path / "pipelines" / "demo-v1.yaml").write_text(PIPELINE)
    return tmp_path


CFG = ReconcilerConfig(public_results_base="http://pub/htr-results", window=20)
NOW = "2026-07-29T09:00:00Z"


def _created_volumes(cluster) -> list[str]:
    return [
        j["metadata"]["labels"]["batch.htrflow/volume"]
        for j in cluster.created
        if j["metadata"]["labels"]["app"] == "htrflow-batch"
    ]


def test_tick_submits_missing_and_writes_status(tmp_path):
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    # R0000001 done; R0000002 + loose submitted
    assert len(cluster.created) == 2
    assert cluster.configmaps["demo-v1"].startswith("steps:")
    camp = doc["campaigns"][0]
    assert camp["totals"] == {
        "done": 1,
        "total": 3,
        "pages_done": 638,
        "pages_total": 638 + 1,
    }
    byid = {v["id"]: v for v in camp["volumes"]}
    assert byid["R0000001"]["status"] == "done"
    assert byid["R0000001"]["viewer_manifest"] == (
        "http://pub/htr-results/demo-v1/R0000001/iiif.json"
    )
    assert byid["R0000002"]["viewer_manifest"] is None
    # synthetic manifest uploaded for the images: volume, and used as source
    assert "sources/demo-v1/loose/manifest.json" in bucket.written
    assert byid["loose"]["source_manifest"].endswith(
        "sources/demo-v1/loose/manifest.json"
    )
    assert doc["generated_at"] == NOW
    assert bucket.written["status/status.json"] == doc


def test_done_volume_carries_updated(tmp_path):
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["updated"] == "2026-08-25T10:00:00Z"
    assert byid["R0000002"]["updated"] is None


def test_tick_respects_window(tmp_path):
    cfg = ReconcilerConfig(public_results_base="http://pub/htr-results", window=1)
    bucket, cluster = FakeBucket(), FakeCluster()
    tick(_repo(tmp_path), bucket, cluster, cfg, NOW)
    assert len(cluster.created) == 1


def test_tick_window_counts_only_jobs_still_in_flight(tmp_path):
    """Terminal Jobs are not in flight.

    A succeeded Job lingers for its 24h TTL exactly like a failed one; counting
    either against the window leaks slots shut as a campaign completes. Only the
    genuinely pending/running Job occupies a slot here, so a window of 2 leaves
    room for exactly one new submission out of the two pending volumes.
    """
    repo = _repo(tmp_path)
    (repo / "campaigns" / "trolldom.yaml").write_text(
        "pipeline: demo-v1\nvolumes: [A, F, S, P1, P2]\n"
    )
    cluster = FakeCluster(
        jobs={
            job_name("demo-v1", "A"): JobState(active=True, failed=False),
            # terminal, exit 13 => needs-attention: out of the lane, and out of
            # the window count too
            job_name("demo-v1", "F"): JobState(active=False, failed=True, exit_code=13),
            job_name("demo-v1", "S"): JobState(
                active=False, failed=False, succeeded=True
            ),
        }
    )
    cfg = ReconcilerConfig(public_results_base="http://pub/htr-results", window=2)
    doc = tick(repo, FakeBucket(), cluster, cfg, NOW)
    assert _created_volumes(cluster) == ["p1"]
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["A"]["status"] == "running"
    assert byid["F"]["status"] == "needs-attention"
    # succeeded but no manifest yet: the done-set is the authority, so it reads
    # queued for the moment it takes the manifest to land
    assert byid["S"]["status"] == "queued"


def test_tick_retries_failed_transient(tmp_path):
    n = job_name("demo-v1", "R0000002")
    cluster = FakeCluster(jobs={n: JobState(active=False, failed=True, exit_code=1)})
    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    # captured logs, deleted the failed job, bumped attempts
    assert bucket.written["status/failures/demo-v1/R0000002.txt"] == "boom traceback"
    assert n in cluster.deleted
    assert bucket.written["status/attempts.json"]["demo-v1/R0000002"] == 1
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "retry"


def test_tick_permanent_failure_needs_attention_not_deleted(tmp_path):
    n = job_name("demo-v1", "R0000002")
    cluster = FakeCluster(jobs={n: JobState(active=False, failed=True, exit_code=13)})
    doc = tick(_repo(tmp_path), FakeBucket(), cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "needs-attention"
    assert n not in cluster.deleted


def test_needs_attention_uploads_log_and_links_it(tmp_path):
    name = job_name("demo-v1", "R0000002")
    jobs = {name: JobState(active=False, failed=True, exit_code=13)}
    bucket, cluster = FakeBucket(), FakeCluster(jobs=jobs)
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "needs-attention"
    assert byid["R0000002"]["failure_log"] == (
        "http://pub/htr-results/status/failures/demo-v1/R0000002.txt"
    )
    assert bucket.written["status/failures/demo-v1/R0000002.txt"] == "boom traceback"
    assert byid["loose"]["failure_log"] is None


def test_done_volume_with_succeeded_job_uploads_run_log(tmp_path):
    name = job_name("demo-v1", "R0000001")
    jobs = {name: JobState(active=False, failed=False, succeeded=True)}
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster(jobs=jobs)
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert bucket.written["status/logs/demo-v1/R0000001.txt"] == "boom traceback"
    assert byid["R0000001"]["run_log"] == (
        "http://pub/htr-results/status/logs/demo-v1/R0000001.txt"
    )


def test_done_volume_without_job_or_log_has_no_run_log(tmp_path):
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["run_log"] is None
    assert "status/logs/demo-v1/R0000001.txt" not in bucket.written


def test_done_volume_with_existing_run_log_is_not_reuploaded(tmp_path):
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()
    bucket.written["status/logs/demo-v1/R0000001.txt"] = "existing content"
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["run_log"] == (
        "http://pub/htr-results/status/logs/demo-v1/R0000001.txt"
    )
    assert bucket.written["status/logs/demo-v1/R0000001.txt"] == "existing content"
    assert bucket.put_text_calls == 0


def test_tick_drift_blocks_pipeline(tmp_path):
    cluster = FakeCluster()
    cluster.configmaps["demo-v1"] = "steps: [OLD]\n"
    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.created == []
    assert any("drift" in w.lower() for w in doc["warnings"])
    # the drift check runs BEFORE ensure_configmap, and a drifted pipeline is
    # never re-applied: the operator's ConfigMap stays exactly as it was.
    assert cluster.configmaps["demo-v1"] == "steps: [OLD]\n"


def test_tick_broken_campaign_contained(tmp_path):
    repo = _repo(tmp_path)
    (repo / "campaigns" / "broken.yaml").write_text("pipeline: [x")
    doc = tick(repo, FakeBucket(), FakeCluster(), CFG, NOW)
    broken = [c for c in doc["campaigns"] if c["name"] == "broken"][0]
    assert broken["error"] is not None
    ok = [c for c in doc["campaigns"] if c["name"] == "trolldom"][0]
    assert ok["error"] is None


def test_tick_prevalidation_blocks_unreachable_without_caching(tmp_path):
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return None  # unreachable

    repo = _repo(tmp_path)
    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "unreachable"
    # unreachable volumes burn no jobs; only the images: volume (no manifest
    # to validate) is submitted
    assert _created_volumes(cluster) == ["loose"]
    assert len(fetched) == 2
    # unreachable is a verdict about the network, not the document: caching it
    # would wedge the volume out of its campaign forever after one flaky fetch
    assert bucket.written["status/validation.json"] == {}
    # so the next tick re-probes, and a recovered source can still be submitted
    fetched.clear()
    doc = tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    assert len(fetched) == 2
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "unreachable"


def test_tick_prevalidation_caches_document_verdicts_forever(tmp_path):
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return {"type": "Collection", "items": [{"id": "http://m/1"}]}

    repo = _repo(tmp_path)
    bucket, cluster = FakeBucket(), FakeCluster()
    tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    assert len(fetched) == 2
    cache = bucket.written["status/validation.json"]
    assert all(v["format"] == "unsupported" for v in cache.values())
    # second tick fetches nothing (cache hit via written -> read_json)
    fetched.clear()
    tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    assert fetched == []


def test_tick_prevalidation_rejects_collections(tmp_path):
    """A P3 Collection has ``items`` too — classifying it as p3 would burn a
    job on a manifest the wrapper cannot read."""

    def fetch_json(url):
        return {
            "type": "Collection",
            "items": [{"id": "http://m/1", "type": "Manifest"}],
        }

    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "unsupported"
    assert byid["R0000002"]["status"] == "unsupported"
    assert _created_volumes(cluster) == ["loose"]


def test_tick_prevalidation_survives_junk_manifests(tmp_path):
    def fetch_json(url):
        return ["not", "a", "manifest"]

    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "unsupported"
    assert byid["R0000001"]["thumbnail"] is None
    assert _created_volumes(cluster) == ["loose"]


def test_tick_prevalidation_extracts_thumbnail(tmp_path):
    def fetch_json(url):
        return {
            "items": [
                {
                    "items": [
                        {
                            "items": [
                                {
                                    "body": {
                                        "id": "http://img/full/max/0/default.jpg",
                                        "service": [{"id": "http://img"}],
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["thumbnail"] == "http://img/full/200,/0/default.jpg"
    assert byid["R0000001"]["status"] == "pending" or byid["R0000001"]["status"] in (
        "running",
        "queued",
    )


def test_tick_done_volume_keeps_cached_thumbnail(tmp_path):
    """A finished volume still needs its picture: the thumbnail comes off the
    validation cache, which is consulted even when the fetch is skipped."""
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return {
            "items": [
                {
                    "items": [
                        {
                            "items": [
                                {"body": {"service": [{"id": "http://img"}]}},
                            ]
                        }
                    ]
                }
            ]
        }

    repo = _repo(tmp_path)
    bucket = FakeBucket()
    tick(repo, bucket, FakeCluster(), CFG, NOW, fetch_json=fetch_json)
    fetched.clear()
    bucket._done.add("R0000001")
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "done"
    assert byid["R0000001"]["thumbnail"] == "http://img/full/200,/0/default.jpg"
    assert fetched == []  # nothing re-fetched: every verdict was cached


def test_tick_attempt_budgets_are_per_pipeline(tmp_path):
    """Re-running a volume under a new pipeline id is the upgrade path, so its
    retry budget must not be inherited from the pipeline it left behind."""
    repo = _repo(tmp_path)
    (repo / "pipelines" / "demo-v2.yaml").write_text(PIPELINE.replace("abc", "def"))
    (repo / "campaigns" / "upgrade.yaml").write_text(
        "pipeline: demo-v2\nvolumes:\n  - R0000002\n"
    )
    failed = JobState(active=False, failed=True, exit_code=1)
    cluster = FakeCluster(
        jobs={
            job_name("demo-v1", "R0000002"): failed,
            job_name("demo-v2", "R0000002"): failed,
        }
    )
    bucket = FakeBucket(stored={"status/attempts.json": {"demo-v1/R0000002": 3}})
    doc = tick(repo, bucket, cluster, CFG, NOW)
    byname = {c["name"]: c for c in doc["campaigns"]}
    old = {v["id"]: v for v in byname["trolldom"]["volumes"]}["R0000002"]
    new = {v["id"]: v for v in byname["upgrade"]["volumes"]}["R0000002"]
    # demo-v1 exhausted its cap of 3; demo-v2 starts fresh and retries
    assert old["status"] == "needs-attention" and old["attempts"] == 3
    assert new["status"] == "retry" and new["attempts"] == 1
    assert bucket.written["status/attempts.json"] == {
        "demo-v1/R0000002": 3,
        "demo-v2/R0000002": 1,
    }
    assert job_name("demo-v1", "R0000002") not in cluster.deleted
    assert job_name("demo-v2", "R0000002") in cluster.deleted


def test_tick_drift_does_not_burn_retry_attempts(tmp_path):
    """A blocked pipeline submits nothing, so it must not spend a volume's
    retry budget for the duration of the block."""
    n = job_name("demo-v1", "R0000002")
    cluster = FakeCluster(jobs={n: JobState(active=False, failed=True, exit_code=1)})
    cluster.configmaps["demo-v1"] = "steps: [OLD]\n"
    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.created == [] and cluster.deleted == []
    assert bucket.written["status/attempts.json"] == {}
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "retry"


def test_tick_unreadable_campaign_is_contained(tmp_path):
    repo = _repo(tmp_path)
    (repo / "campaigns" / "binary.yaml").write_bytes(b"pipeline: \xff\xfe\n")
    cluster = FakeCluster()
    doc = tick(repo, FakeBucket(), cluster, CFG, NOW)
    binary = [c for c in doc["campaigns"] if c["name"] == "binary"][0]
    assert binary["error"] is not None
    ok = [c for c in doc["campaigns"] if c["name"] == "trolldom"][0]
    assert ok["error"] is None
    assert len(cluster.created) == 3  # the healthy campaign still runs


def test_tick_unreadable_pipeline_warns_and_contains(tmp_path):
    repo = _repo(tmp_path)
    (repo / "pipelines" / "other-v1.yaml").write_bytes(b"image: \xff\n")
    cluster = FakeCluster()
    doc = tick(repo, FakeBucket(), cluster, CFG, NOW)
    assert any("other-v1" in w for w in doc["warnings"])
    assert len(cluster.created) == 3


def test_tick_reports_orphans(tmp_path):
    bucket = FakeBucket(done={"R0000001", "ghost-vol"})
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    assert doc["campaigns"][0]["orphans"] == ["ghost-vol"]


def test_tick_orphans_span_campaigns_sharing_a_pipeline(tmp_path):
    """Orphans are per-PIPELINE: a volume claimed by a sibling campaign on the
    same pipeline is not an orphan, and the list is attached once."""
    repo = _repo(tmp_path)
    (repo / "campaigns" / "zzz-sibling.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R0000009\n"
    )
    bucket = FakeBucket(done={"R0000009", "ghost-vol"})
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW)
    byname = {c["name"]: c for c in doc["campaigns"]}
    assert byname["trolldom"]["orphans"] == ["ghost-vol"]
    assert byname["zzz-sibling"]["orphans"] == []


def test_tick_unknown_pipeline_is_contained(tmp_path):
    repo = _repo(tmp_path)
    (repo / "campaigns" / "ghost.yaml").write_text("pipeline: nope-v1\nvolumes: [R1]\n")
    doc = tick(repo, FakeBucket(), FakeCluster(), CFG, NOW)
    ghost = [c for c in doc["campaigns"] if c["name"] == "ghost"][0]
    assert ghost["error"] is not None and "nope-v1" in ghost["error"]
    assert ghost["orphans"] == []
    assert ghost["pipeline_yaml"] is None


def _p3_manifest(n: int) -> dict:
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "type": "Manifest",
        "items": [
            {
                "type": "Canvas",
                "items": [{"items": [{"body": {"id": f"http://x/{i}.jpg"}}]}],
            }
            for i in range(n)
        ],
    }


def test_validate_caches_page_count(tmp_path):
    bucket, cluster = FakeBucket(), FakeCluster()
    fetched = {}

    def fetch(url):
        fetched[url] = fetched.get(url, 0) + 1
        return _p3_manifest(3)

    tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch)
    validation = bucket.written["status/validation.json"]
    ref = "https://lbiiif.riksarkivet.se/arkis!R0000002/manifest"
    assert validation[ref]["page_count"] == 3


def test_status_carries_repo_url_steps_and_page_totals(tmp_path):
    cfg = ReconcilerConfig(
        public_results_base="http://pub/htr-results",
        window=20,
        campaigns_repo_url="git://example/campaigns",
    )
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()

    def fetch(url):
        return _p3_manifest(4)

    doc = tick(_repo(tmp_path), bucket, cluster, cfg, NOW, fetch_json=fetch)
    assert doc["campaigns_repo_url"] == "git://example/campaigns"
    camp = doc["campaigns"][0]
    assert camp["pipeline_steps"] == ["Segmentation"]
    assert camp["pipeline_yaml"].startswith("steps:")
    byid = {v["id"]: v for v in camp["volumes"]}
    assert byid["loose"]["pages_total"] == 1  # len(images)
    assert byid["R0000002"]["pages_total"] == 4  # canvas count from fetch
    assert byid["R0000001"]["pages_total"] == 638  # done fallback = pages_done
    assert camp["totals"]["pages_total"] == 638 + 4 + 1
    assert camp["totals"]["pages_done"] == 638


def test_repo_web_url_overrides_clone_url(tmp_path):
    cfg = ReconcilerConfig(
        public_results_base="http://pub/htr-results",
        campaigns_repo_url="git://internal/campaigns",
        campaigns_repo_web_url="https://github.com/example/campaigns",
    )
    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, cfg, NOW)
    assert doc["campaigns_repo_url"] == "https://github.com/example/campaigns"


def test_page_totals_null_when_unknown(tmp_path):
    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)  # no fetch_json
    camp = doc["campaigns"][0]
    byid = {v["id"]: v for v in camp["volumes"]}
    assert byid["R0000002"]["pages_total"] is None
    assert byid["loose"]["pages_total"] == 1
    assert camp["totals"]["pages_total"] == 1
    assert camp["totals"]["pages_done"] is None


def test_synthetic_manifest_job_url_uses_internal_base(tmp_path):
    cfg = ReconcilerConfig(
        public_results_base="http://localhost:30900/htr-results",
        internal_results_base="http://rustfs.ns.svc:9000/htr-results",
        window=20,
    )
    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, cfg, NOW)
    loose_job = next(
        j
        for j in cluster.created
        if j["metadata"]["labels"]["batch.htrflow/volume"] == "loose"
    )
    env = {
        e["name"]: e.get("value")
        for e in loose_job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["IIIF_MANIFEST_URL"] == (
        "http://rustfs.ns.svc:9000/htr-results/sources/demo-v1/loose/manifest.json"
    )
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["loose"]["source_manifest"] == (
        "http://localhost:30900/htr-results/sources/demo-v1/loose/manifest.json"
    )
    written = bucket.written["sources/demo-v1/loose/manifest.json"]
    assert written["id"].startswith("http://localhost:30900/")


def test_internal_base_falls_back_to_public(tmp_path):
    bucket, cluster = FakeBucket(), FakeCluster()
    tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    loose_job = next(
        j
        for j in cluster.created
        if j["metadata"]["labels"]["batch.htrflow/volume"] == "loose"
    )
    env = {
        e["name"]: e.get("value")
        for e in loose_job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["IIIF_MANIFEST_URL"].startswith("http://pub/htr-results/sources/")


def _warmups_created(cluster) -> list[str]:
    return [
        j["metadata"]["name"]
        for j in cluster.created
        if j["metadata"]["labels"]["app"] == "htrflow-warmup"
    ]


def test_tick_warms_a_new_pipeline_before_submitting_its_volumes(tmp_path):
    """A pipeline whose cache is not yet warm gets its warm-up Job and nothing
    else: batch Jobs run offline on a read-only cache, so submitting them
    first would only burn attempts on ``model not found``."""
    bucket, cluster = FakeBucket(), FakeCluster(warmups={})
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert _warmups_created(cluster) == ["htr-warmup-demo-v1"]
    assert _created_volumes(cluster) == []
    assert any("warm" in w.lower() and "demo-v1" in w for w in doc["warnings"])
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "pending"


def test_tick_waits_while_warmup_runs(tmp_path):
    running = JobState(active=True, failed=False)
    cluster = FakeCluster(warmups={"demo-v1": running})
    tick(_repo(tmp_path), FakeBucket(), cluster, CFG, NOW)
    assert cluster.created == []


def test_tick_submits_once_warmup_succeeded(tmp_path):
    cluster = FakeCluster(warmups={"demo-v1": WARMED})
    doc = tick(_repo(tmp_path), FakeBucket(), cluster, CFG, NOW)
    assert _warmups_created(cluster) == []
    assert len(_created_volumes(cluster)) == 3
    assert not any("warm" in w.lower() for w in doc["warnings"])


def test_tick_failed_warmup_is_logged_deleted_and_retried_next_tick(tmp_path):
    failed = JobState(active=False, failed=True, exit_code=1)
    bucket, cluster = FakeBucket(), FakeCluster(warmups={"demo-v1": failed})
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.deleted == ["htr-warmup-demo-v1"]
    assert cluster.created == []
    assert bucket.written["status/warmup/demo-v1.log"] == "boom traceback"
    assert any("warm" in w.lower() and "failed" in w.lower() for w in doc["warnings"])


def test_tick_drifted_pipeline_is_not_warmed(tmp_path):
    cluster = FakeCluster(warmups={})
    cluster.configmaps["demo-v1"] = "steps: [OLD]\n"
    tick(_repo(tmp_path), FakeBucket(), cluster, CFG, NOW)
    assert cluster.created == []


def test_tick_finished_pipeline_is_never_warmed(tmp_path):
    """Warm-up is lazy: a pipeline whose campaigns are all done has nothing to
    submit, so warming it would only burn CPU — and, for pipelines pinned to
    images that predate the warm-up entrypoint, fail and be recreated on
    every tick forever."""
    bucket = FakeBucket(done={"R0000001", "R0000002", "loose"})
    cluster = FakeCluster(warmups={})
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.created == []
    assert not any("warm" in w.lower() for w in doc["warnings"])


def test_synthetic_volume_thumbnail_is_first_image(tmp_path):
    """A volume declared with images: has no IIIF service, so its thumbnail is
    the first image itself — the same fallback _thumbnail applies to
    service-less external manifests."""
    doc = tick(_repo(tmp_path), FakeBucket(), FakeCluster(), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["loose"]["thumbnail"] == "http://x/1.jpg"


def test_in_cluster_manifest_urls_are_rewritten_for_the_browser(tmp_path):
    """status.json is browser-facing: a manifest hosted on the in-cluster S3
    endpoint (any bucket) is shown under the public endpoint, while the Job
    still fetches the in-cluster URL."""
    repo = _repo(tmp_path)
    (repo / "campaigns" / "trolldom.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n"
        "  - id: fixture\n"
        "    manifest: http://rustfs.ns.svc:9000/htr-fixtures/mock/manifest.json\n"
    )
    cfg = ReconcilerConfig(
        public_results_base="http://localhost:30900/htr-results",
        internal_results_base="http://rustfs.ns.svc:9000/htr-results",
        window=20,
    )

    def fetch_json(url):
        assert url.startswith("http://rustfs.ns.svc:9000/")
        return {
            "items": [
                {
                    "items": [
                        {
                            "items": [
                                {
                                    "body": {
                                        "id": "http://rustfs.ns.svc:9000/htr-fixtures/mock/0001.jpg"
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    cluster = FakeCluster()
    doc = tick(repo, FakeBucket(), cluster, cfg, NOW, fetch_json=fetch_json)
    vol = doc["campaigns"][0]["volumes"][0]
    assert vol["source_manifest"] == (
        "http://localhost:30900/htr-fixtures/mock/manifest.json"
    )
    assert vol["thumbnail"] == "http://localhost:30900/htr-fixtures/mock/0001.jpg"
    env = {
        e["name"]: e.get("value")
        for e in cluster.created[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["IIIF_MANIFEST_URL"] == (
        "http://rustfs.ns.svc:9000/htr-fixtures/mock/manifest.json"
    )


def test_browser_url_is_identity_without_internal_base():
    from htrflow_reconciler.main import _browser_url

    url = "http://rustfs.ns.svc:9000/htr-fixtures/mock/manifest.json"
    assert _browser_url(url, CFG) == url
    assert _browser_url(None, CFG) is None
    cfg = ReconcilerConfig(
        public_results_base="http://localhost:30900/htr-results",
        internal_results_base="http://rustfs.ns.svc:9000/htr-results",
    )
    assert _browser_url("https://lbiiif.riksarkivet.se/x/manifest", cfg) == (
        "https://lbiiif.riksarkivet.se/x/manifest"
    )
