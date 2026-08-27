from pathlib import Path

from htrflow_reconciler import s3 as keys
from htrflow_reconciler.jobspec import ReconcilerConfig
from htrflow_reconciler.main import SourceRejected, tick
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
        self.calls = 0  # every S3 round trip, like the real Bucket
        self.read_keys: list[str] = []

    def done_volumes(self, pipeline_id):
        self.calls += 1 + len(self._done)
        return {v: "2026-08-25T10:00:00Z" for v in self._done}

    def read_json(self, key):
        self.calls += 1
        self.read_keys.append(key)
        return self.stored.get(key) or self.written.get(key)

    def write_json(self, key, obj):
        self.calls += 1
        self.written[key] = obj

    def put_text(self, key, text):
        self.calls += 1
        self.written[key] = text
        self.put_text_calls += 1

    def exists(self, key):
        self.calls += 1
        return key in self.written

    def read_text(self, key):
        self.calls += 1
        v = self.written.get(key)
        return v if isinstance(v, str) else None

    def delete(self, key):
        self.calls += 1
        self.written.pop(key, None)
        self.deleted = getattr(self, "deleted", []) + [key]

    def count_pages(self, pipeline_id, volume_id):
        self.calls += 1
        return 638 if volume_id in self._done else 0


WARMED = JobState(active=False, failed=False, succeeded=True)


class _AllWarmed(dict):
    """Default warm-up snapshot: every pipeline's cache is already filled, so
    the submission tests below exercise submission, not the warm-up gate."""

    def get(self, key, default=None):
        return WARMED


class FakeCluster:
    def __init__(self, jobs=None, warmups=None, lease_free=True):
        self._jobs = jobs or {}
        self._warmups = warmups
        self._lease_free = lease_free
        self.created, self.deleted, self.configmaps = [], [], {}
        self.leases = []

    def acquire_lease(self, name, duration_seconds):
        self.leases.append(("acquire", name))
        return self._lease_free

    def release_lease(self, name):
        self.leases.append(("release", name))

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
    key = keys.synthetic_manifest_key("demo-v1", "loose", ["http://x/1.jpg"])
    assert key in bucket.written
    assert byid["loose"]["source_manifest"].endswith(key)
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
    assert bucket.written["status/attempts.json"]["demo-v1/R0000002"]["n"] == 1
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


def test_tick_prevalidation_blocks_unreachable_and_backs_off(tmp_path):
    """``unreachable`` is a verdict about the NETWORK: it is cached for a few
    ticks (a dead host must not cost every tick a timeout, X1/S5) but never
    forever — a recovered source is re-probed and can still be submitted."""
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return None  # unreachable

    repo = _repo(tmp_path)
    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    byid = _rows(doc)
    assert byid["R0000001"]["status"] == "unreachable"
    # unreachable volumes burn no jobs; only the images: volume (no manifest
    # to validate) is submitted
    assert _created_volumes(cluster) == ["loose"]
    assert len(fetched) == 2
    cached = bucket.written["status/validation.json"]
    ref = "https://lbiiif.riksarkivet.se/arkis!R0000001/manifest"
    assert cached[ref]["format"] == "unreachable"
    # NOW + unreachable_ticks * tick_seconds (3 * 300 s)
    assert cached[ref]["unreachable_until"] == "2026-07-29T09:15:00Z"
    # within the back-off window: no fetch, still blocked
    fetched.clear()
    doc = tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    assert fetched == []
    assert _rows(doc)["R0000001"]["status"] == "unreachable"
    # after it: re-probed
    later = "2026-07-29T09:15:00Z"
    doc = tick(repo, bucket, cluster, CFG, later, fetch_json=fetch_json)
    assert len(fetched) == 2


def test_tick_prevalidation_is_bounded_per_tick(tmp_path):
    """X1: validation is O(new volumes), not O(volumes), and happens in a
    bounded batch per tick; volumes not yet validated wait (pending, not
    submitted) rather than being submitted blind."""
    repo = _repo(tmp_path)
    (repo / "campaigns" / "trolldom.yaml").write_text(
        "pipeline: demo-v1\nvolumes: [V1, V2, V3, V4, V5]\n"
    )
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return _p3_manifest(2)

    cfg = ReconcilerConfig(
        public_results_base="http://pub/htr-results", max_validations_per_tick=2
    )
    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(repo, bucket, cluster, cfg, NOW, fetch_json=fetch_json)
    assert len(fetched) == 2
    assert sorted(_created_volumes(cluster)) == ["v1", "v2"]
    assert _rows(doc)["V3"]["status"] == "pending"
    assert len(bucket.written["status/validation.json"]) == 2
    doc = tick(repo, bucket, FakeCluster(), cfg, NOW, fetch_json=fetch_json)
    assert len(fetched) == 4


def test_validation_json_is_persisted_before_any_submission(tmp_path):
    """A deadline-killed tick must not lose the fetches it already paid for."""
    events = []

    class LoggingBucket(FakeBucket):
        def write_json(self, key, obj):
            events.append(("write", key))
            super().write_json(key, obj)

    class LoggingCluster(FakeCluster):
        def create_job(self, job):
            events.append(("create", job["metadata"]["name"]))
            super().create_job(job)

    def fetch_json(url):
        return _p3_manifest(1)

    bucket, cluster = LoggingBucket(), LoggingCluster()
    tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    first_write = events.index(("write", "status/validation.json"))
    first_create = next(i for i, e in enumerate(events) if e[0] == "create")
    assert first_write < first_create


class CountingBucket(FakeBucket):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.count_calls, self.exists_calls, self.read_calls = [], [], []

    def count_pages(self, pipeline_id, volume_id):
        self.count_calls.append(volume_id)
        return super().count_pages(pipeline_id, volume_id)

    def exists(self, key):
        self.exists_calls.append(key)
        return super().exists(key)

    def read_json(self, key):
        self.read_calls.append(key)
        return super().read_json(key)


def test_done_volumes_are_not_reprobed_once_cached(tmp_path):
    """X1: a done volume is immutable under its manifest mtime; its page count
    and run-log presence are cached in status/volumes.json keyed by that
    mtime, so the steady state costs no LIST/HEAD per done volume."""
    repo = _repo(tmp_path)
    bucket = CountingBucket(done={"R0000001"})
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW)
    assert bucket.count_calls.count("R0000001") == 1
    assert _rows(doc)["R0000001"]["pages_done"] == 638
    cache = bucket.written["status/volumes.json"]
    assert cache["demo-v1/R0000001"]["pages"] == 638
    assert cache["demo-v1/R0000001"]["updated"] == "2026-08-25T10:00:00Z"
    bucket.count_calls.clear()
    bucket.exists_calls.clear()
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW)
    assert "R0000001" not in bucket.count_calls  # submissions probe their own
    assert not [k for k in bucket.exists_calls if k.startswith("status/logs/")]
    assert _rows(doc)["R0000001"]["pages_done"] == 638
    assert _rows(doc)["R0000001"]["run_log"] is None


def test_done_volume_cache_invalidates_on_a_new_manifest_mtime(tmp_path):
    class MovingBucket(CountingBucket):
        mtime = "2026-08-25T10:00:00Z"

        def done_volumes(self, pipeline_id):
            return {v: self.mtime for v in self._done}

    repo = _repo(tmp_path)
    bucket = MovingBucket(done={"R0000001"})
    tick(repo, bucket, FakeCluster(), CFG, NOW)
    bucket.mtime = "2026-08-26T10:00:00Z"  # re-published (operator re-run)
    bucket.count_calls.clear()
    tick(repo, bucket, FakeCluster(), CFG, NOW)
    assert bucket.count_calls.count("R0000001") == 1


def test_done_volume_run_log_link_is_cached(tmp_path):
    repo = _repo(tmp_path)
    bucket = CountingBucket(done={"R0000001"})
    bucket.written["status/logs/demo-v1/R0000001.txt"] = "shipped"
    tick(repo, bucket, FakeCluster(), CFG, NOW)
    bucket.exists_calls.clear()
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW)
    assert bucket.exists_calls == []
    run_log = _rows(doc)["R0000001"]["run_log"]
    assert run_log.endswith("status/logs/demo-v1/R0000001.txt")


def test_synthetic_manifest_is_written_once_without_a_get_per_tick(tmp_path):
    repo = _repo(tmp_path)
    bucket = CountingBucket()
    tick(repo, bucket, FakeCluster(), CFG, NOW)
    key = keys.synthetic_manifest_key("demo-v1", "loose", ["http://x/1.jpg"])
    assert key in bucket.written
    bucket.read_calls.clear()
    written_before = dict(bucket.written)
    tick(repo, bucket, FakeCluster(), CFG, NOW)
    assert key not in bucket.read_calls
    assert bucket.written[key] == written_before[key]


def test_tick_summary_in_status_and_one_log_line(tmp_path, caplog):
    """O5: a green tick says what it did."""
    jobs = _failed_job("R0000002", exit_code=1)
    bucket = FakeBucket(done={"R0000001"})
    with caplog.at_level("INFO"):
        doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    summary = doc["tick_summary"]
    assert set(summary) == {
        "seconds",
        "s3_calls",
        "validations",
        "submitted",
        "retried",
    }
    assert summary["submitted"] == 1  # loose; R0000002 re-enters next tick
    assert summary["retried"] == 1
    assert summary["validations"] == 0
    assert summary["s3_calls"] == bucket.calls
    assert doc["tick_seconds"] == CFG.tick_seconds == 300
    lines = [r.message for r in caplog.records if r.message.startswith("tick:")]
    assert len(lines) == 1 and "submitted=1" in lines[0] and "retried=1" in lines[0]


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
    written = bucket.written["status/attempts.json"]
    assert written["demo-v1/R0000002"] == {"n": 3, "terminal": "capped"}
    assert written["demo-v2/R0000002"] == {"n": 1, "terminal": None}
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
    key = keys.synthetic_manifest_key("demo-v1", "loose", ["http://x/1.jpg"])
    assert env["IIIF_MANIFEST_URL"] == f"http://rustfs.ns.svc:9000/htr-results/{key}"
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert (
        byid["loose"]["source_manifest"] == f"http://localhost:30900/htr-results/{key}"
    )
    written = bucket.written[key]
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


def test_service_less_volumes_have_no_thumbnail(tmp_path):
    """F2: a thumbnail is a SIZED request or nothing. A volume declared with
    images: has no IIIF service, and a service-less external manifest offers
    only full-size scans — 6.7 MB to paint eight 26 px pictures. The
    frontend shows a placeholder for null."""

    def fetch_json(url):
        return {
            "items": [{"items": [{"items": [{"body": {"id": "http://big/1.jpg"}}]}]}]
        }

    doc = tick(
        _repo(tmp_path), FakeBucket(), FakeCluster(), CFG, NOW, fetch_json=fetch_json
    )
    byid = _rows(doc)
    assert byid["loose"]["thumbnail"] is None
    assert byid["R0000001"]["thumbnail"] is None


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
                                        "id": "http://rustfs.ns.svc:9000/htr-fixtures/mock/0001.jpg",
                                        "service": [
                                            {
                                                "id": "http://rustfs.ns.svc:9000/iiif/mock-0001"
                                            }
                                        ],
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
    assert vol["thumbnail"] == (
        "http://localhost:30900/iiif/mock-0001/full/200,/0/default.jpg"
    )
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


def test_running_volume_with_shipped_log_gets_live_run_log(tmp_path):
    """The wrapper ships status/logs/<pid>/<vid>.txt while it runs; the link
    must not wait for done."""
    name = job_name("demo-v1", "R0000001")
    jobs = {name: JobState(active=True, failed=False)}
    bucket, cluster = FakeBucket(), FakeCluster(jobs=jobs)
    bucket.written["status/logs/demo-v1/R0000001.txt"] = "streaming..."
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "running"
    assert byid["R0000001"]["run_log"] == (
        "http://pub/htr-results/status/logs/demo-v1/R0000001.txt"
    )
    assert bucket.put_text_calls == 0


def test_running_volume_without_shipped_log_has_no_run_log(tmp_path):
    name = job_name("demo-v1", "R0000001")
    jobs = {name: JobState(active=True, failed=False)}
    doc = tick(_repo(tmp_path), FakeBucket(), FakeCluster(jobs=jobs), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["run_log"] is None


def test_pending_volume_never_probes_for_a_run_log(tmp_path):
    class CountingBucket(FakeBucket):
        def __init__(self):
            super().__init__()
            self.exists_calls = []

        def exists(self, key):
            self.exists_calls.append(key)
            return super().exists(key)

    bucket = CountingBucket()
    tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    assert not [k for k in bucket.exists_calls if k.startswith("status/logs/")]


def _failed_job(vid="R0000001", **kw):
    return {job_name("demo-v1", vid): JobState(active=False, failed=True, **kw)}


def test_retry_preserves_shipped_log_as_failure_evidence_and_retires_it(tmp_path):
    bucket = FakeBucket()
    bucket.written["status/logs/demo-v1/R0000001.txt"] = "full shipped log\nERROR boom"
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=_failed_job()), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "retry"
    assert bucket.written["status/failures/demo-v1/R0000001.txt"] == (
        "full shipped log\nERROR boom"
    )
    assert "status/logs/demo-v1/R0000001.txt" not in bucket.written
    assert byid["R0000001"]["run_log"] is None


def test_retry_without_shipped_log_falls_back_to_kube_tail(tmp_path):
    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=_failed_job()), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "retry"
    assert bucket.written["status/failures/demo-v1/R0000001.txt"] == "boom traceback"
    assert not getattr(bucket, "deleted", [])


def test_needs_attention_copies_shipped_log_but_keeps_it(tmp_path):
    bucket = FakeBucket()
    bucket.written["status/logs/demo-v1/R0000001.txt"] = "shipped, exit 13"
    jobs = _failed_job(exit_code=13)
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "needs-attention"
    assert bucket.written["status/failures/demo-v1/R0000001.txt"] == "shipped, exit 13"
    assert bucket.written["status/logs/demo-v1/R0000001.txt"] == "shipped, exit 13"
    assert byid["R0000001"]["run_log"] is None  # failure_log carries it


def test_run_manifest_for_in_flight_and_done_only(tmp_path):
    name = job_name("demo-v1", "R0000001")
    jobs = {name: JobState(active=True, failed=False)}
    bucket = FakeBucket(done={"R0000002"})
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["run_manifest"] == (
        "http://pub/htr-results/demo-v1/R0000001/manifest.json"
    )
    assert byid["R0000002"]["run_manifest"] == (
        "http://pub/htr-results/demo-v1/R0000002/manifest.json"
    )
    assert byid["loose"]["status"] in ("pending", "queued", "running")
    if byid["loose"]["status"] == "pending":
        assert byid["loose"]["run_manifest"] is None


def _rows(doc, campaign=0):
    return {v["id"]: v for v in doc["campaigns"][campaign]["volumes"]}


def test_exit_13_persists_a_terminal_record(tmp_path):
    """R1: the verdict lands in attempts.json the tick it is first derived."""
    bucket = FakeBucket()
    jobs = _failed_job(exit_code=13)
    tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"] == {
        "n": 0,
        "terminal": "exit-13",
    }
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    assert _rows(doc)["R0000001"]["terminal"] == "exit-13"


def test_capped_volume_persists_a_terminal_record(tmp_path):
    bucket = FakeBucket(stored={"status/attempts.json": {"demo-v1/R0000001": 3}})
    jobs = _failed_job(exit_code=1)
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    assert _rows(doc)["R0000001"]["status"] == "needs-attention"
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"] == {
        "n": 3,
        "terminal": "capped",
    }


def test_terminal_record_keeps_a_reaped_volume_out_of_the_lane(tmp_path):
    """R1: the Job is gone (24h TTL); the volume must not be resubmitted."""
    record = {"n": 0, "terminal": "exit-13"}
    stored = {"status/attempts.json": {"demo-v1/R0000001": record}}
    bucket, cluster = FakeBucket(stored=stored), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    row = _rows(doc)["R0000001"]
    assert row["status"] == "needs-attention"
    assert row["terminal"] == "exit-13"
    assert row["failure_log"].endswith("status/failures/demo-v1/R0000001.txt")
    assert "r0000001" not in _created_volumes(cluster)
    # the record survives the tick untouched
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"] == record


def test_v1_attempt_ints_are_migrated_on_read(tmp_path):
    bucket = FakeBucket(stored={"status/attempts.json": {"demo-v1/R0000001": 1}})
    jobs = _failed_job(exit_code=1)
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    assert _rows(doc)["R0000001"]["attempts"] == 2
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"] == {
        "n": 2,
        "terminal": None,
    }


def test_retry_bump_is_persisted_before_the_job_is_deleted(tmp_path):
    """R3: the bump must survive an abort between bump and delete — the tick
    deadline, an S3 hiccup, a kube error — otherwise the attempt is free and
    the cap is never reached."""

    class ExplodingCluster(FakeCluster):
        def delete_job(self, name):
            raise RuntimeError("kube-apiserver 503")

    bucket = FakeBucket()
    cluster = ExplodingCluster(jobs=_failed_job(exit_code=1))
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"]["n"] == 1
    row = _rows(doc)["R0000001"]
    assert row["status"] == "retry"
    assert row["error"] is not None and "503" in row["error"]
    # one volume's failure never takes the tick down: the others still run
    assert sorted(_created_volumes(cluster)) == ["loose", "r0000002"]
    assert bucket.written["status/status.json"] == doc


def test_deleting_job_is_neither_bumped_nor_resubmitted(tmp_path):
    """R2: a Foreground delete can outlive the tick (pod stuck Terminating);
    the Job is still listed Failed, so without this it is charged again on
    every tick until the cap with no re-run in between."""
    jobs = {
        job_name("demo-v1", "R0000001"): JobState(
            active=False,
            failed=True,
            exit_code=1,
            deletion_timestamp="2026-07-29T08:59:00Z",
        )
    }
    stored = {"status/attempts.json": {"demo-v1/R0000001": 1}}
    bucket, cluster = FakeBucket(stored=stored), FakeCluster(jobs=jobs)
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    row = _rows(doc)["R0000001"]
    assert row["status"] == "deleting"
    assert row["attempts"] == 1
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"]["n"] == 1
    assert cluster.deleted == []
    assert "r0000001" not in _created_volumes(cluster)


def test_deleting_job_still_occupies_a_window_slot(tmp_path):
    """Its pod may still hold the GPU while Terminating."""
    jobs = {
        job_name("demo-v1", "R0000001"): JobState(
            active=False, failed=True, deletion_timestamp="2026-07-29T08:59:00Z"
        )
    }
    cfg = ReconcilerConfig(public_results_base="http://pub/htr-results", window=1)
    cluster = FakeCluster(jobs=jobs)
    tick(_repo(tmp_path), FakeBucket(), cluster, cfg, NOW)
    assert cluster.created == []


def test_per_volume_error_is_contained(tmp_path):
    class FlakyBucket(FakeBucket):
        def count_pages(self, pipeline_id, volume_id):
            if volume_id == "R0000001":
                raise RuntimeError("S3 500")
            return super().count_pages(pipeline_id, volume_id)

    jobs = {job_name("demo-v1", "R0000001"): JobState(active=True, failed=False)}
    bucket = FlakyBucket()
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    rows = _rows(doc)
    assert rows["R0000001"]["status"] == "running"
    assert "S3 500" in rows["R0000001"]["error"]
    assert rows["R0000002"]["error"] is None
    assert bucket.written["status/status.json"] == doc


def test_duplicate_submission_is_contained(tmp_path):
    """The real adapter swallows 409; a fake that raises on a duplicate name
    proves the tick contains a create failure per submission."""

    class StrictCluster(FakeCluster):
        def create_job(self, job):
            if any(
                j["metadata"]["name"] == job["metadata"]["name"] for j in self.created
            ):
                raise RuntimeError("409 AlreadyExists")
            super().create_job(job)

    repo = _repo(tmp_path)
    (repo / "campaigns" / "twin.yaml").write_text(
        "pipeline: demo-v1\nvolumes: [R0000002]\n"
    )
    cluster = StrictCluster()
    doc = tick(repo, FakeBucket(), cluster, CFG, NOW)
    assert _created_volumes(cluster).count("r0000002") == 1
    assert any("409" in w for w in doc["warnings"])


def test_tick_is_skipped_while_the_lease_is_held(tmp_path, caplog):
    """O8: a manual ``kubectl create job --from`` bypasses concurrencyPolicy;
    two ticks in flight would double-submit and double-charge attempts."""
    bucket, cluster = FakeBucket(), FakeCluster(lease_free=False)
    with caplog.at_level("WARNING"):
        doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert doc["skipped"] == "lease held"
    assert cluster.created == [] and bucket.written == {}
    assert any("lease" in r.message.lower() for r in caplog.records)


def test_tick_takes_and_releases_the_named_lease(tmp_path):
    cfg = ReconcilerConfig(
        public_results_base="http://pub/htr-results", lease_name="htr-rec-prod"
    )
    cluster = FakeCluster()
    tick(_repo(tmp_path), FakeBucket(), cluster, cfg, NOW)
    assert cluster.leases == [("acquire", "htr-rec-prod"), ("release", "htr-rec-prod")]


def test_healthy_rows_carry_terminal_null(tmp_path):
    bucket = FakeBucket(done={"R0000001"})
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    assert all(v["terminal"] is None for v in doc["campaigns"][0]["volumes"])


# -- R5 fairness ---------------------------------------------------------------


def test_lanes_are_ordered_by_per_campaign_in_flight_count(tmp_path):
    """R5: round-robin used to restart from the alphabetically first campaign
    every tick, so a big campaign with work in flight kept winning the free
    slot. Campaigns with fewer Jobs in flight go first."""
    repo = _repo(tmp_path)
    (repo / "campaigns" / "aaa-big.yaml").write_text(
        "pipeline: demo-v1\nvolumes: [B1, B2, B3]\n"
    )
    (repo / "campaigns" / "zzz-small.yaml").write_text(
        "pipeline: demo-v1\nvolumes: [S1]\n"
    )
    (repo / "campaigns" / "trolldom.yaml").unlink()
    running = JobState(active=True, failed=False, campaign="aaa-big")
    jobs = {job_name("demo-v1", "B1"): running, job_name("demo-v1", "B2"): running}
    cfg = ReconcilerConfig(public_results_base="http://pub/htr-results", window=3)
    cluster = FakeCluster(jobs=jobs)
    tick(repo, FakeBucket(), cluster, cfg, NOW)
    assert _created_volumes(cluster) == ["s1"]


def test_submitted_jobs_carry_the_campaign_label(tmp_path):
    cluster = FakeCluster()
    tick(_repo(tmp_path), FakeBucket(), cluster, CFG, NOW)
    labels = cluster.created[0]["metadata"]["labels"]
    assert labels["batch.htrflow/campaign"] == "trolldom"


# -- R7 warm-up budget ---------------------------------------------------------


def test_warmup_exit_13_is_terminal_and_never_recreated(tmp_path):
    """R7: a warm-up that says 'permanent' (bad model id, bad YAML) used to be
    deleted and recreated every tick forever."""
    failed = JobState(active=False, failed=True, exit_code=13)
    bucket, cluster = FakeBucket(), FakeCluster(warmups={"demo-v1": failed})
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.deleted == [] and cluster.created == []
    assert bucket.written["status/attempts.json"]["warmup/demo-v1"] == {
        "n": 1,
        "terminal": "exit-13",
    }
    assert bucket.written["status/warmup/demo-v1.log"] == "boom traceback"
    assert any("attention" in w.lower() and "demo-v1" in w for w in doc["warnings"])
    # Job reaped later: still nothing is created
    cluster = FakeCluster(warmups={})
    tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.created == []


def test_warmup_retries_are_capped(tmp_path):
    failed = JobState(active=False, failed=True, exit_code=1)
    stored = {"status/attempts.json": {"warmup/demo-v1": {"n": 2, "terminal": None}}}
    bucket, cluster = (
        FakeBucket(stored=stored),
        FakeCluster(warmups={"demo-v1": failed}),
    )
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert bucket.written["status/attempts.json"]["warmup/demo-v1"] == {
        "n": 3,
        "terminal": "capped",
    }
    assert cluster.deleted == []
    assert any("attention" in w.lower() for w in doc["warnings"])


def test_warmup_transient_failure_is_counted_and_retried(tmp_path):
    failed = JobState(active=False, failed=True, exit_code=1)
    bucket, cluster = FakeBucket(), FakeCluster(warmups={"demo-v1": failed})
    tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert cluster.deleted == ["htr-warmup-demo-v1"]
    assert bucket.written["status/attempts.json"]["warmup/demo-v1"]["n"] == 1


# -- R8 corrupt owned JSON -----------------------------------------------------


def test_corrupt_owned_json_is_a_warning_not_a_poison_pill(tmp_path):
    class CorruptBucket(FakeBucket):
        def read_json(self, key):
            if key == "status/attempts.json":
                raise ValueError("Expecting value: line 1 column 1")
            if key == "status/validation.json":
                return ["not", "a", "mapping"]
            return super().read_json(key)

    bucket, cluster = CorruptBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    assert len(cluster.created) == 3
    assert any("attempts.json" in w for w in doc["warnings"])
    assert any("validation.json" in w for w in doc["warnings"])
    # nothing inherited from the corrupt file: only this tick's submissions
    assert all(r["n"] == 0 for r in bucket.written["status/attempts.json"].values())


# -- R9 images: edits take effect ---------------------------------------------


def test_synthetic_manifest_key_follows_the_image_list(tmp_path):
    repo = _repo(tmp_path)
    bucket = FakeBucket()
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW)
    first = _rows(doc)["loose"]["source_manifest"]
    (repo / "campaigns" / "trolldom.yaml").write_text(
        CAMPAIGN.replace("[http://x/1.jpg]", "[http://x/1.jpg, http://x/2.jpg]")
    )
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW)
    second = _rows(doc)["loose"]["source_manifest"]
    assert first != second
    key = second.split("http://pub/htr-results/", 1)[1]
    assert len(bucket.written[key]["items"]) == 2
    assert _rows(doc)["loose"]["pages_total"] == 2


# -- R14 orphans ---------------------------------------------------------------


def test_orphans_exclude_ids_declared_by_a_broken_sibling(tmp_path):
    """R14: a campaign that fails to parse still DECLARES its volumes; those
    results are not orphans just because a sibling entry is malformed."""
    repo = _repo(tmp_path)
    (repo / "campaigns" / "zzz-broken.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R0000009\n  - id: 'a/b'\n    manifest: http://x\n"
    )
    bucket = FakeBucket(done={"R0000009", "ghost-vol"})
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW)
    byname = {c["name"]: c for c in doc["campaigns"]}
    assert byname["zzz-broken"]["error"] is not None
    assert byname["trolldom"]["orphans"] == ["ghost-vol"]


# -- S1 at tick level ----------------------------------------------------------


def test_empty_image_allow_list_is_warned_about(tmp_path):
    doc = tick(_repo(tmp_path), FakeBucket(), FakeCluster(), CFG, NOW)
    assert any("allow" in w.lower() and "image" in w.lower() for w in doc["warnings"])


def test_pipeline_outside_the_allow_list_submits_nothing(tmp_path):
    cfg = ReconcilerConfig(
        public_results_base="http://pub/htr-results",
        allowed_image_repos=("ghcr.io/riksarkivet/",),
    )
    cluster = FakeCluster()
    doc = tick(_repo(tmp_path), FakeBucket(), cluster, cfg, NOW)
    assert cluster.created == []
    assert any("demo-v1" in w and "allow" in w.lower() for w in doc["warnings"])
    assert not any("allow-list empty" in w for w in doc["warnings"])
    assert doc["campaigns"][0]["error"] is not None  # unknown pipeline


# -- O2 at tick level ----------------------------------------------------------


def test_submission_records_pages_done_and_passes_the_page_count(tmp_path):
    def fetch_json(url):
        return _p3_manifest(1000)

    bucket, cluster = FakeBucket(), FakeCluster()
    tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    byvol = {
        j["metadata"]["labels"]["batch.htrflow/volume"]: j for j in cluster.created
    }
    assert byvol["r0000001"]["spec"]["activeDeadlineSeconds"] == 30000
    assert byvol["loose"]["spec"]["activeDeadlineSeconds"] == 21600
    att = bucket.written["status/attempts.json"]
    assert att["demo-v1/R0000001"] == {"n": 0, "terminal": None, "pages_at_submit": 0}


def test_deadline_failure_with_progress_is_not_charged(tmp_path):
    """O2: a long volume that hit its deadline but advanced pages_done is
    resumed (the wrapper skips done pages), not charged an attempt."""

    class ProgressBucket(FakeBucket):
        def count_pages(self, pipeline_id, volume_id):
            return 400 if volume_id == "R0000001" else 0

    jobs = {
        job_name("demo-v1", "R0000001"): JobState(
            active=False, failed=True, exit_code=None, reason="DeadlineExceeded"
        )
    }
    stored = {
        "status/attempts.json": {
            "demo-v1/R0000001": {"n": 1, "terminal": None, "pages_at_submit": 100}
        }
    }
    bucket = ProgressBucket(stored=stored)
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    assert _rows(doc)["R0000001"]["status"] == "retry"
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"]["n"] == 1


def test_deadline_failure_without_progress_is_charged(tmp_path):
    jobs = {
        job_name("demo-v1", "R0000001"): JobState(
            active=False, failed=True, exit_code=None, reason="DeadlineExceeded"
        )
    }
    stored = {
        "status/attempts.json": {
            "demo-v1/R0000001": {"n": 1, "terminal": None, "pages_at_submit": 0}
        }
    }
    bucket = FakeBucket(stored=stored)
    tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"]["n"] == 2


def test_p2_thumbnail_is_sized_or_null():
    from htrflow_reconciler.main import _thumbnail

    with_service = {
        "sequences": [
            {
                "canvases": [
                    {
                        "images": [
                            {
                                "resource": {
                                    "@id": "http://img/full/full/0/default.jpg",
                                    "service": {"@id": "http://img"},
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    assert _thumbnail(with_service) == "http://img/full/200,/0/default.jpg"
    without = {
        "sequences": [{"canvases": [{"images": [{"resource": {"@id": "http://x"}}]}]}]
    }
    assert _thumbnail(without) is None


# -- wrapper contract (A2): SIGTERM, permanent vs transient sources -----------


def test_sigterm_143_with_progress_is_not_charged(tmp_path):
    """The wrapper exits 143 on SIGTERM (deadline kill, drain) after writing
    its termination log; like DeadlineExceeded it is resumed for free when
    pages_done advanced."""

    class ProgressBucket(FakeBucket):
        def count_pages(self, pipeline_id, volume_id):
            return 400 if volume_id == "R0000001" else 0

    jobs = {
        job_name("demo-v1", "R0000001"): JobState(
            active=False, failed=True, exit_code=143, reason="BackoffLimitExceeded"
        )
    }
    stored = {
        "status/attempts.json": {
            "demo-v1/R0000001": {"n": 1, "terminal": None, "pages_at_submit": 100}
        }
    }
    bucket = ProgressBucket(stored=stored)
    doc = tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    assert _rows(doc)["R0000001"]["status"] == "retry"
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"]["n"] == 1


def test_sigterm_143_without_progress_is_charged(tmp_path):
    jobs = {
        job_name("demo-v1", "R0000001"): JobState(
            active=False, failed=True, exit_code=143
        )
    }
    stored = {
        "status/attempts.json": {
            "demo-v1/R0000001": {"n": 1, "terminal": None, "pages_at_submit": 0}
        }
    }
    bucket = FakeBucket(stored=stored)
    tick(_repo(tmp_path), bucket, FakeCluster(jobs=jobs), CFG, NOW)
    assert bucket.written["status/attempts.json"]["demo-v1/R0000001"]["n"] == 2


def test_permanent_source_rejections_are_cached_forever(tmp_path):
    """Pre-validation mirrors the wrapper's permanent set: a 404 or a non-JSON
    body will not become a manifest by being asked again, so — unlike a 503
    — it is never re-probed."""
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        if url.endswith("R0000001/manifest"):
            raise SourceRejected("unreachable")  # 404
        raise SourceRejected("unsupported")  # HTML body

    repo = _repo(tmp_path)
    bucket = FakeBucket()
    doc = tick(repo, bucket, FakeCluster(), CFG, NOW, fetch_json=fetch_json)
    rows = _rows(doc)
    assert rows["R0000001"]["status"] == "unreachable"
    assert rows["R0000002"]["status"] == "unsupported"
    cached = bucket.written["status/validation.json"]
    assert all("unreachable_until" not in v for v in cached.values())
    fetched.clear()
    much_later = "2027-01-01T00:00:00Z"
    tick(repo, bucket, FakeCluster(), CFG, much_later, fetch_json=fetch_json)
    assert fetched == []


def test_done_volume_thumbnail_comes_from_the_wrapper_manifest(tmp_path):
    """The wrapper writes <pid>/<vid>/thumb.jpg and records it in
    manifest.json; the reconciler advertises it (browser URL) and caches the
    answer with the volume record, so the steady state costs no S3 call."""
    bucket = FakeBucket(done={"R0000001"})
    bucket.stored["demo-v1/R0000001/manifest.json"] = {"thumbnail": "thumb.jpg"}
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert (
        byid["R0000001"]["thumbnail"]
        == "http://pub/htr-results/demo-v1/R0000001/thumb.jpg"
    )
    vcache = bucket.read_json("status/volumes.json")["demo-v1/R0000001"]
    assert vcache["thumb"] is True
    calls_before = bucket.calls
    tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    # second tick: the volume record is reused, no manifest read
    assert not any(
        k == "demo-v1/R0000001/manifest.json" for k in bucket.read_keys[calls_before:]
    )


def test_done_volume_without_wrapper_thumbnail_keeps_the_iiif_one(tmp_path):
    bucket = FakeBucket(done={"R0000001"})
    bucket.stored["demo-v1/R0000001/manifest.json"] = {"pages": 1}
    bucket.stored["status/validation.json"] = {
        "https://lbiiif.riksarkivet.se/arkis!R0000001/manifest": {
            "format": "p3",
            "thumbnail": "http://img/full/200,/0/default.jpg",
            "page_count": 1,
            "checked_at": NOW,
        }
    }
    doc = tick(_repo(tmp_path), bucket, FakeCluster(), CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["thumbnail"] == "http://img/full/200,/0/default.jpg"
