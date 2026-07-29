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

    def done_volumes(self, pipeline_id):
        return set(self._done)

    def read_json(self, key):
        return self.stored.get(key) or self.written.get(key)

    def write_json(self, key, obj):
        self.written[key] = obj

    def put_text(self, key, text):
        self.written[key] = text

    def count_pages(self, pipeline_id, volume_id):
        return 638 if volume_id in self._done else 0


class FakeCluster:
    def __init__(self, jobs=None):
        self._jobs = jobs or {}
        self.created, self.deleted, self.configmaps = [], [], {}

    def jobs(self):
        return dict(self._jobs)

    def create_job(self, job):
        self.created.append(job)

    def delete_job(self, name):
        self.deleted.append(name)

    def get_configmap_steps(self, pipeline_id):
        return self.configmaps.get(pipeline_id)

    def ensure_configmap(self, pipeline_id, steps_yaml):
        self.configmaps[pipeline_id] = steps_yaml

    def failed_job_logs(self, name, tail=50):
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
    return [j["metadata"]["labels"]["batch.htrflow/volume"] for j in cluster.created]


def test_tick_submits_missing_and_writes_status(tmp_path):
    bucket, cluster = FakeBucket(done={"R0000001"}), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    # R0000001 done; R0000002 + loose submitted
    assert len(cluster.created) == 2
    assert cluster.configmaps["demo-v1"].startswith("steps:")
    camp = doc["campaigns"][0]
    assert camp["totals"] == {"done": 1, "total": 3}
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


def test_tick_respects_window(tmp_path):
    cfg = ReconcilerConfig(public_results_base="http://pub/htr-results", window=1)
    bucket, cluster = FakeBucket(), FakeCluster()
    tick(_repo(tmp_path), bucket, cluster, cfg, NOW)
    assert len(cluster.created) == 1


def test_tick_retries_failed_transient(tmp_path):
    n = job_name("demo-v1", "R0000002")
    cluster = FakeCluster(jobs={n: JobState(active=False, failed=True, exit_code=1)})
    bucket = FakeBucket()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW)
    # captured logs, deleted the failed job, bumped attempts
    assert bucket.written["status/failures/demo-v1/R0000002.txt"] == "boom traceback"
    assert n in cluster.deleted
    assert bucket.written["status/attempts.json"]["R0000002"] == 1
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "retry"


def test_tick_permanent_failure_needs_attention_not_deleted(tmp_path):
    n = job_name("demo-v1", "R0000002")
    cluster = FakeCluster(jobs={n: JobState(active=False, failed=True, exit_code=13)})
    doc = tick(_repo(tmp_path), FakeBucket(), cluster, CFG, NOW)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000002"]["status"] == "needs-attention"
    assert n not in cluster.deleted


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


def test_tick_prevalidation_blocks_unreachable_and_caches(tmp_path):
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return None  # unreachable

    bucket, cluster = FakeBucket(), FakeCluster()
    doc = tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    byid = {v["id"]: v for v in doc["campaigns"][0]["volumes"]}
    assert byid["R0000001"]["status"] == "unreachable"
    # unreachable volumes burn no jobs; only the images: volume (no manifest
    # to validate) is submitted
    assert _created_volumes(cluster) == ["loose"]
    # verdicts cached: both manifest URLs fetched exactly once, cache written
    assert len(fetched) == 2
    cache = bucket.written["status/validation.json"]
    assert all(v["format"] == "unreachable" for v in cache.values())
    # second tick fetches nothing (cache hit via written -> read_json)
    fetched.clear()
    tick(_repo(tmp_path), bucket, cluster, CFG, NOW, fetch_json=fetch_json)
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
