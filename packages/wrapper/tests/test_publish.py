from pathlib import Path

from htrflow_batch import publish
from htrflow_batch.config import Config
from htrflow_batch.iiif import PageRef
from htrflow_batch.stream import PageOutcome, StreamStats

PIPELINE = "steps: []\n"
PIPELINE_SHA = "315b81de5a786a8106206c4da56557e62ebd1907bf9a7345d7bec96eccdbc104"


def _pages():
    return [
        PageRef(
            index=1,
            name="0001",
            image_url="https://user:pw@iiif.example/p1/full/2500,/0/default.jpg?t=S3CRET",
            canvas={"id": "https://iiif.example/p1/canvas"},
        ),
        PageRef(
            index=2,
            name="0002",
            image_url="https://iiif.example/p2/full/2500,/0/default.jpg",
            canvas={"@id": "https://iiif.example/p2/canvas"},  # P2 shape
        ),
    ]


def test_run_manifest_is_the_completion_marker_field_for_field(cfg, monkeypatch):
    """manifest.json is what every reader keys on (resume, the read API, the
    Phase 2 gate): pin the whole body, redaction included."""
    monkeypatch.setattr(publish, "_htrflow_version", lambda: "0.2.3")
    stats = StreamStats(
        results={
            "0001": PageOutcome(status="ok", seconds=1.234),
            "0002": PageOutcome(
                status="failed",
                error="fetch https://user:pw@iiif.example/x?token=S3CRET failed",
            ),
        },
        stall_seconds=2.34,
    )

    body = publish.run_manifest(
        cfg,
        {"IMAGE_DIGEST": "sha256:abc"},
        _pages(),
        stats,
        "https://iiif.example/mock-vol/manifest.json",
        PIPELINE,
        12.34,
        4096,
    )

    assert body == {
        "volume": "SE-RA-1234",
        "pipeline_id": "demo-v1",
        "pipeline_sha256": PIPELINE_SHA,
        "pipeline_yaml": PIPELINE,
        "htrflow_version": "0.2.3",
        "image_digest": "sha256:abc",
        "pages": 2,
        "results": {
            "0001": {"status": "ok", "seconds": 1.23},
            "0002": {
                "status": "failed",
                "seconds": 0.0,
                "error": "fetch https://iiif.example/x failed",
            },
        },
        "source_manifest": "https://iiif.example/mock-vol/manifest.json",
        "page_sources": {
            "0001": "https://iiif.example/p1/full/2500,/0/default.jpg",
            "0002": "https://iiif.example/p2/full/2500,/0/default.jpg",
        },
        "canvas_ids": {
            "0001": "https://iiif.example/p1/canvas",
            "0002": "https://iiif.example/p2/canvas",
        },
        "max_image_width": 2500,
        "bytes_fetched": 4096,
        "wall_seconds": 12.3,
        "gpu_stall_seconds": 2.3,
        "pages_per_second": 0.081,
        "viewer_url": "http://public/htr-results/demo-v1/SE-RA-1234/iiif.json",
    }
    assert "S3CRET" not in str(body)  # S6: the bucket is world-readable
    assert "IMAGE_DIGEST" not in body  # unknown when the Job does not set it


def test_run_manifest_without_an_image_digest_or_a_wall_clock(cfg):
    body = publish.run_manifest(
        cfg, {}, _pages(), StreamStats(), "https://x/manifest.json", PIPELINE, 0.0, 0
    )
    assert body["image_digest"] == "unknown"
    assert body["pages_per_second"] == 0  # no division by a zero wall clock
    assert body["results"] == {}


ALTO = '<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'


def _cfg(tmp_path):
    (tmp_path / "pipeline.yaml").write_text(PIPELINE)
    return Config.from_env(
        {
            "VOLUME_REF": "SE-RA-1234",
            "IIIF_MANIFEST_URL": "https://iiif.example/mock-vol/manifest.json",
            "PIPELINE_PATH": str(tmp_path / "pipeline.yaml"),
            "PIPELINE_ID": "demo-v1",
            "S3_BUCKET": "htr-results",
            "PUBLIC_RESULTS_BASE": "http://public/htr-results",
            "WORKDIR_PATH": str(tmp_path / "work"),
        }
    )


class _SlowStore:
    """A store whose reads and writes cost measurable time on a fake clock."""

    def __init__(self, clock):
        self.clock = clock
        self.puts = {}

    def get_bytes(self, key):  # a resumed page's stored ALTO
        self.clock["now"] += 1.0
        return ALTO.encode()

    def put_json(self, key, obj):
        self.clock["now"] += 2.0
        self.puts[key] = obj

    def put_text(self, key, text, content_type):
        self.clock["now"] += 4.0
        self.puts[key] = text


def test_wall_seconds_spans_the_publish_uploads(tmp_path, monkeypatch):
    """wall_seconds is snapshotted after iiif.json and pipeline.yaml are
    written, not before the stage: on a resumed volume, reading stored ALTO
    back and writing the viewer manifest is real time the run spent."""
    clock = {"now": 100.0}
    monkeypatch.setattr(publish.time, "monotonic", lambda: clock["now"])
    cfg = _cfg(tmp_path)
    alto_dir = Path(cfg.workdir) / "outputs" / "alto"
    alto_dir.mkdir(parents=True)
    (alto_dir / "0002.xml").write_text(ALTO)  # this run wrote page 2
    store = _SlowStore(clock)

    publish.run(
        cfg,
        {},
        store,
        {"label": {"none": ["vol"]}, "items": []},
        "https://iiif.example/mock-vol/manifest.json",
        _pages(),
        StreamStats(results={"0002": PageOutcome(status="ok", seconds=1.0)}),
        {"0001"},  # page 1 was published by an earlier run
        100.0,  # t_start
        4096,
    )

    # 1 s reading page 1's stored ALTO + 2 s iiif.json + 4 s pipeline.yaml,
    # snapshotted before manifest.json's own 2 s PUT.
    assert store.puts["manifest.json"]["wall_seconds"] == 7.0
    assert store.puts["manifest.json"]["pages_per_second"] == round(1 / 7.0, 3)
    assert list(store.puts) == ["iiif.json", "pipeline.yaml", "manifest.json"]
