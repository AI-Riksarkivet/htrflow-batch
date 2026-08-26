import json

import pytest

from htrflow_reconciler.parse import (
    PipelineError,
    parse_campaign,
    parse_pipeline,
    step_summaries,
)

CAMPAIGN = """
pipeline: demo-v1
volumes:
  - R0001203
  - id: dodsbok-1698
    manifest: https://iiif.example.org/xyz/manifest
  - id: loose-scans
    images:
      - https://example.org/scan1.jpg
      - https://example.org/scan2.jpg
"""


def test_parse_campaign_three_forms():
    c = parse_campaign("trolldom", CAMPAIGN)
    assert c.error is None
    assert c.pipeline_id == "demo-v1"
    v = {x.id: x for x in c.volumes}
    assert v["R0001203"].manifest_url == (
        "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest"
    )
    assert v["dodsbok-1698"].manifest_url == "https://iiif.example.org/xyz/manifest"
    assert v["loose-scans"].images == (
        "https://example.org/scan1.jpg",
        "https://example.org/scan2.jpg",
    )


def test_parse_campaign_malformed_yaml_is_contained():
    c = parse_campaign("bad", "pipeline: [unclosed")
    assert c.error is not None
    assert c.volumes == []


def test_parse_campaign_rejects_unsafe_id():
    c = parse_campaign(
        "bad", "pipeline: p\nvolumes:\n  - id: 'a/b'\n    manifest: http://x\n"
    )
    assert c.error is not None and "a/b" in c.error


def test_parse_campaign_rejects_duplicate_ids():
    c = parse_campaign("dup", "pipeline: p\nvolumes:\n  - R1\n  - R1\n")
    assert c.error is not None and "R1" in c.error


def _one_volume(vid: str) -> str:
    # json.dumps gives a YAML double-quoted scalar, so "\n" really is a newline.
    return f"pipeline: p\nvolumes:\n  - id: {json.dumps(vid)}\n    manifest: http://x\n"


@pytest.mark.parametrize(
    "vid",
    ["abc\n", ".hidden", "trailing-", "-leading", "a" * 64, "a/b", ""],
)
def test_parse_campaign_rejects_bad_volume_ids(vid):
    c = parse_campaign("bad", _one_volume(vid))
    assert c.error is not None and "volume id" in c.error


@pytest.mark.parametrize(
    "vid", ["R0001203", "dodsbok-1698", "loose-scans", "a", "a.b_c-d9", "a" * 63]
)
def test_parse_campaign_accepts_good_volume_ids(vid):
    c = parse_campaign("ok", _one_volume(vid))
    assert c.error is None
    assert [v.id for v in c.volumes] == [vid]


PIPELINE = """
image: docker.io/riksarkivet/htrflow-batch@sha256:abc123
steps:
  - step: Segmentation
"""


def test_parse_pipeline_extracts_image_and_steps_hash():
    p = parse_pipeline("demo-v1", PIPELINE)
    assert p.image.endswith("@sha256:abc123")
    assert "Segmentation" in p.steps_yaml
    assert "image:" not in p.steps_yaml  # ConfigMap gets steps only (spec §3)
    assert len(p.steps_sha256) == 64


def test_parse_pipeline_rejects_tag_image():
    with pytest.raises(PipelineError, match="digest"):
        parse_pipeline("demo-v1", "image: repo/img:v5\nsteps: []\n")


def test_parse_pipeline_requires_steps():
    with pytest.raises(PipelineError, match="steps"):
        parse_pipeline("demo-v1", "image: r/i@sha256:a\n")


@pytest.mark.parametrize(
    "pid", ["Demo-v1", "demo_v1", "demo-v1\n", "-demo", "demo-", "d" * 64, ""]
)
def test_parse_pipeline_rejects_non_dns1123_id(pid):
    # ConfigMap names are DNS-1123 labels, so uppercase/underscores are out.
    with pytest.raises(PipelineError, match="unsafe pipeline id"):
        parse_pipeline(pid, PIPELINE)


def test_parse_pipeline_accepts_dns1123_id():
    assert parse_pipeline("demo-v1", PIPELINE).id == "demo-v1"


def test_step_summaries_full_form():
    yaml_text = """steps:
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
    assert step_summaries(yaml_text) == [
        "Segmentation: yolo (Riksarkivet/yolov9-regions-1)",
        "TextRecognition: TrOCR (Riksarkivet/trocr-base-handwritten-hist-swe-2)",
    ]


def test_step_summaries_fallbacks():
    yaml_text = """steps:
  - step: Export
  - step: Segmentation
    settings:
      model: yolo
"""
    assert step_summaries(yaml_text) == ["Export", "Segmentation: yolo"]


def test_step_summaries_junk_is_empty():
    assert step_summaries("steps: notalist") == []
    assert step_summaries(": not yaml [") == []


def test_parse_pipeline_hash_is_canonical_json_of_the_steps():
    """R10: the hash covers the parsed steps in canonical JSON, so a PyYAML
    upgrade that re-flows the dump cannot read as drift. The yaml-dump sha
    is kept for results published before this change."""
    import hashlib

    a = parse_pipeline("demo-v1", PIPELINE)
    b = parse_pipeline(
        "demo-v1",
        PIPELINE.replace("  - step: Segmentation", "  - {step: Segmentation}"),
    )
    assert a.steps_sha256 == b.steps_sha256
    canonical = json.dumps(
        {"steps": [{"step": "Segmentation"}]}, sort_keys=True, separators=(",", ":")
    )
    assert a.steps_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    assert a.legacy_sha256 == hashlib.sha256(a.steps_yaml.encode()).hexdigest()


def test_broken_campaign_still_declares_its_volume_ids():
    """R14: orphan detection needs the ids a malformed campaign meant to claim."""
    c = parse_campaign(
        "bad",
        "pipeline: demo-v1\nvolumes:\n  - R1\n  - id: 'a/b'\n    manifest: http://x\n",
    )
    assert c.error is not None
    assert c.pipeline_id == "demo-v1"
    assert c.declared_ids == ("R1", "a/b")


# -- S1: the campaigns repo is a code-execution boundary -----------------------

ALLOWED = ("ghcr.io/riksarkivet/", "docker.io/riksarkivet/htrflow-batch")


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/riksarkivet/htrflow-batch@sha256:abc",
        "ghcr.io/riksarkivet/anything/deeper@sha256:abc",
        "docker.io/riksarkivet/htrflow-batch@sha256:abc",
    ],
)
def test_parse_pipeline_accepts_images_under_allowed_repos(image):
    p = parse_pipeline("p", f"image: {image}\nsteps: []\n", allowed_repos=ALLOWED)
    assert p.image == image


@pytest.mark.parametrize(
    "image",
    [
        "docker.io/evil/htrflow-batch@sha256:abc",
        "ghcr.io/riksarkivet-evil/x@sha256:abc",  # prefix on a path boundary
        "docker.io/riksarkivet/htrflow-batch-evil@sha256:abc",
        "riksarkivet/htrflow-batch@sha256:abc",  # implicit registry is not listed
    ],
)
def test_parse_pipeline_rejects_images_outside_allowed_repos(image):
    with pytest.raises(PipelineError, match="allow"):
        parse_pipeline("p", f"image: {image}\nsteps: []\n", allowed_repos=ALLOWED)


def test_parse_pipeline_empty_allow_list_accepts_any_digest_pinned_image():
    p = parse_pipeline("p", "image: anyone/anything@sha256:abc\nsteps: []\n")
    assert p.image.startswith("anyone/")


STEPS_WITH_MODELS = """image: r/i@sha256:abc
steps:
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
        {rev1}
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
        {rev2}
  - step: Export
"""


def test_parse_pipeline_requires_a_40_hex_revision_per_model_when_enabled():
    good = STEPS_WITH_MODELS.format(
        rev1="revision: " + "a" * 40, rev2="revision: " + "b" * 40
    )
    parse_pipeline("p", good, require_revision=True)
    missing = STEPS_WITH_MODELS.format(rev1="revision: " + "a" * 40, rev2="")
    with pytest.raises(PipelineError, match="revision"):
        parse_pipeline("p", missing, require_revision=True)
    short = STEPS_WITH_MODELS.format(
        rev1="revision: main", rev2="revision: " + "b" * 40
    )
    with pytest.raises(PipelineError, match="revision"):
        parse_pipeline("p", short, require_revision=True)
    # off by default: unpinned models pass
    parse_pipeline("p", missing)


# -- S4/S5: only http(s) sources reach the fetches and the browser ------------


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///etc/passwd", "ftp://x/m", "//x/m", "x/m", ""],
)
def test_parse_campaign_rejects_non_http_manifest(url):
    c = parse_campaign(
        "bad", f"pipeline: p\nvolumes:\n  - id: v\n    manifest: {json.dumps(url)}\n"
    )
    assert c.error is not None


def test_parse_campaign_rejects_non_http_images():
    text = (
        "pipeline: p\nvolumes:\n  - id: v\n"
        "    images: [https://x/1.jpg, 'javascript:alert(1)']\n"
    )
    c = parse_campaign("bad", text)
    assert c.error is not None and "javascript" in c.error
