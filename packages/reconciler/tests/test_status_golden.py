"""Golden status.json (audit T5): one tick over a fixture that produces every
volume status, compared byte-for-byte against
``fixtures/status.golden.json``. The golden file is the reconciler's half of
the reconciler<->frontend contract — the campaign browser parses the same
file with its zod schema in strict mode — so a shape change here is a
deliberate, reviewed change on both sides.

Regenerate after an intentional change: ``UPDATE_GOLDEN=1 uv run pytest
packages/reconciler/tests/test_status_golden.py``.
"""

import json
import os
from pathlib import Path

import pytest

from htrflow_reconciler.jobspec import ReconcilerConfig
from htrflow_reconciler.main import SourceRejected, tick
from htrflow_reconciler.status import JobState, job_name

GOLDEN = Path(__file__).parent / "fixtures" / "status.golden.json"
NOW = "2026-08-26T10:00:00Z"
PID = "demo-v1"

#: Every status ``derive`` / ``volume_row`` can emit. The fixture must keep
#: producing all of them, so the golden stays a complete contract sample.
ALL_STATUSES = {
    "done",
    "running",
    "queued",
    "retry",
    "needs-attention",
    "pending",
    "unreachable",
    "unsupported",
    "deleting",
}

PIPELINE = """image: r/htrflow-batch@sha256:0123456789abcdef
steps:
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
"""

CAMPAIGN = """pipeline: demo-v1
volumes:
  - vol-done
  - vol-running
  - vol-queued
  - vol-retry
  - vol-attn
  - vol-capped
  - vol-sticky
  - vol-pending
  - vol-unreachable
  - vol-unsupported
  - vol-deleting
  - id: vol-images
    images: [http://img.example/1.jpg, http://img.example/2.jpg]
"""

CFG = ReconcilerConfig(
    public_results_base="http://localhost:30900/htr-results",
    internal_results_base="http://rustfs.htr-batch.svc:9000/htr-results",
    campaigns_repo_url="git://git-daemon.htr-batch.svc/campaigns-local.git",
    campaigns_repo_web_url="https://github.com/example/campaigns",
    allowed_image_repos=("r/",),
    window=20,
)


def _p3(url: str) -> dict:
    """A two-canvas P3 manifest whose image service is derived from ``url``."""
    service = url.rsplit("/", 1)[0].replace("lbiiif", "iiif-image") + "/img"
    canvases = []
    for i in (1, 2):
        canvases.append(
            {
                "id": f"{url}/canvas/{i}",
                "type": "Canvas",
                "items": [
                    {
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "type": "Annotation",
                                "motivation": "painting",
                                "body": {
                                    "id": f"{service}{i}/full/max/0/default.jpg",
                                    "type": "Image",
                                    "service": [
                                        {"id": f"{service}{i}", "type": "ImageService3"}
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        )
    return {"type": "Manifest", "id": url, "items": canvases}


def fetch_json(url: str):
    if "vol-unreachable" in url:
        return None  # 5xx / network: transient, backed off
    if "vol-unsupported" in url:
        raise SourceRejected("unsupported")  # HTML body: cached forever
    return _p3(url)


class GoldenBucket:
    def __init__(self, done: dict[str, str], pages: dict[str, int], stored: dict):
        self._done = done
        self._pages = pages
        self.stored = stored
        self.written: dict = {}
        self.calls = 0

    def done_volumes(self, pipeline_id):
        self.calls += 1 + len(self._done)
        return dict(self._done)

    def read_json(self, key):
        self.calls += 1
        return self.written.get(key, self.stored.get(key))

    def write_json(self, key, obj):
        self.calls += 1
        self.written[key] = obj

    def put_text(self, key, text):
        self.calls += 1
        self.written[key] = text

    def read_text(self, key):
        self.calls += 1
        v = self.written.get(key, self.stored.get(key))
        return v if isinstance(v, str) else None

    def exists(self, key):
        self.calls += 1
        return key in self.written or key in self.stored

    def delete(self, key):
        self.calls += 1
        self.written.pop(key, None)
        self.stored.pop(key, None)

    def count_pages(self, pipeline_id, volume_id):
        self.calls += 1
        return self._pages.get(volume_id, 0)


class GoldenCluster:
    def __init__(self, jobs: dict[str, JobState]):
        self._jobs = jobs
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.configmaps: dict[str, str] = {}

    def acquire_lease(self, name, duration_seconds):
        return True

    def release_lease(self, name):
        pass

    def jobs(self):
        return dict(self._jobs)

    def warmups(self):
        return {PID: JobState(active=False, failed=False, succeeded=True)}

    def create_job(self, job):
        self.created.append(job)

    def delete_job(self, name):
        self.deleted.append(name)

    def get_configmap_steps(self, pipeline_id):
        return self.configmaps.get(pipeline_id)

    def ensure_configmap(self, pipeline_id, steps_yaml):
        self.configmaps[pipeline_id] = steps_yaml

    def job_logs(self, name, tail=50):
        return f"kube tail of {name} ({tail} lines)\n"


def _failed(exit_code: int, reason: str, **kw) -> JobState:
    return JobState(active=False, failed=True, exit_code=exit_code, reason=reason, **kw)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "campaigns" / "golden.yaml").write_text(CAMPAIGN)
    (tmp_path / "campaigns" / "broken.yaml").write_text("pipeline: [unclosed\n")
    (tmp_path / "campaigns" / "ghost.yaml").write_text(
        "pipeline: ghost-v9\nvolumes: [vol-ghost]\n"
    )
    (tmp_path / "pipelines" / f"{PID}.yaml").write_text(PIPELINE)
    return tmp_path


@pytest.fixture
def world():
    def jn(vid: str) -> str:
        return job_name(PID, vid)

    jobs = {
        jn("vol-running"): JobState(active=True, failed=False, campaign="golden"),
        jn("vol-queued"): JobState(active=False, failed=False, campaign="golden"),
        jn("vol-retry"): _failed(1, "BackoffLimitExceeded", campaign="golden"),
        jn("vol-attn"): _failed(13, "PodFailurePolicy", campaign="golden"),
        jn("vol-capped"): _failed(1, "BackoffLimitExceeded", campaign="golden"),
        jn("vol-deleting"): _failed(
            1,
            "BackoffLimitExceeded",
            campaign="golden",
            deletion_timestamp="2026-08-26T09:59:30+00:00",
        ),
    }
    stored = {
        "status/attempts.json": {
            f"{PID}/vol-retry": {"n": 0, "terminal": None, "pages_at_submit": 0},
            # at the cap already: this tick's failure parks it as "capped"
            f"{PID}/vol-capped": {"n": 3, "terminal": None},
            f"{PID}/vol-sticky": {"n": 3, "terminal": "capped"},
        },
        f"status/logs/{PID}/vol-running.txt": "2026-08-26 09:58:00,000 INFO live\n",
        f"status/logs/{PID}/vol-retry.txt": "2026-08-26 09:57:00,000 ERROR boom\n",
    }
    bucket = GoldenBucket(
        done={"vol-done": "2026-08-25T10:00:00Z", "vol-orphan": "2026-08-24T10:00:00Z"},
        pages={"vol-done": 2, "vol-running": 1},
        stored=stored,
    )
    return bucket, GoldenCluster(jobs)


def _normalise(doc: dict) -> dict:
    """Wall-clock fields are not part of the contract."""
    out = json.loads(json.dumps(doc))
    out["tick_summary"]["seconds"] = 0.0
    return out


def test_status_json_matches_the_golden(repo, world):
    bucket, cluster = world
    doc = _normalise(tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json))
    rendered = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(rendered)
    assert GOLDEN.exists(), "run once with UPDATE_GOLDEN=1 to create the golden"
    assert json.loads(GOLDEN.read_text()) == doc, (
        "status.json drifted from the golden; if intended, regenerate with "
        "UPDATE_GOLDEN=1 and update the frontend schema/fixture together"
    )


def test_fixture_covers_every_status(repo, world):
    bucket, cluster = world
    doc = tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    golden = next(c for c in doc["campaigns"] if c["name"] == "golden")
    statuses = {v["status"] for v in golden["volumes"]}
    assert statuses == ALL_STATUSES
    byid = {v["id"]: v for v in golden["volumes"]}
    # the two ways a volume becomes needs-attention, and the sticky record
    assert byid["vol-attn"]["terminal"] == "exit-13"
    assert byid["vol-capped"]["terminal"] == "capped"
    assert byid["vol-sticky"]["terminal"] == "capped"
    assert golden["orphans"] == ["vol-orphan"]
    assert {c["name"]: bool(c["error"]) for c in doc["campaigns"]} == {
        "broken": True,
        "ghost": True,
        "golden": False,
    }


def test_golden_side_effects(repo, world):
    """What the golden tick did to the cluster and S3 — pinned alongside the
    document so the fixture cannot silently stop exercising a path."""
    bucket, cluster = world
    tick(repo, bucket, cluster, CFG, NOW, fetch_json=fetch_json)
    created = sorted(
        j["metadata"]["labels"]["batch.htrflow/volume"] for j in cluster.created
    )
    assert created == ["vol-images", "vol-pending"]
    assert cluster.deleted == [job_name(PID, "vol-retry")]
    assert set(bucket.written) >= {
        "status/status.json",
        "status/attempts.json",
        "status/validation.json",
        "status/volumes.json",
        f"status/failures/{PID}/vol-retry.txt",
        f"status/failures/{PID}/vol-attn.txt",
        f"status/failures/{PID}/vol-capped.txt",
    }
