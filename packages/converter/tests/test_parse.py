from pathlib import Path

import pytest

from htrflow_converter.models import Volume
from htrflow_converter.parse import ValidationError, load

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"


def _load(root: Path):
    return load(root / "campaigns", root / "pipelines", root / "converter.yaml")


def test_good_fixture_loads_campaigns_and_pipelines():
    campaigns, pipelines, cfg = _load(GOOD)
    assert len(campaigns) == 2
    assert len(pipelines) == 1
    assert "demo-v1" in pipelines
    assert cfg.namespace == "htr-test"
    assert cfg.window == 10


def test_good_fixture_bare_id_expands_with_source_template():
    campaigns, _, cfg = _load(GOOD)
    kyrk = next(c for c in campaigns if c.name == "kyrk")
    v = {v.id: v for v in kyrk.volumes}
    assert v["R0001203"].manifest == cfg.source_template.format(ref="R0001203")
    assert v["R0001203"].images == []


def test_good_fixture_images_volume_kept():
    campaigns, _, _ = _load(GOOD)
    kyrk = next(c for c in campaigns if c.name == "kyrk")
    v = {v.id: v for v in kyrk.volumes}
    assert v["loose-scans"].manifest is None
    assert v["loose-scans"].images == [
        "https://example.org/scan1.jpg",
        "https://example.org/scan2.jpg",
    ]
    assert v["dodsbok-1698"].manifest == "https://iiif.example.org/xyz/manifest"


def test_good_fixture_second_campaign_priority_and_window():
    campaigns, _, _ = _load(GOOD)
    loc = next(c for c in campaigns if c.name == "loc")
    assert loc.priority == "high"
    assert loc.window == 5


@pytest.mark.parametrize(
    "case,substring",
    [
        ("unsafe-volume-id", "unsafe volume id"),
        ("duplicate-volume-id", "duplicate volume id"),
        ("bad-url", "must be an http(s) URL"),
        ("unknown-pipeline", "unknown pipeline"),
        ("bad-image", "image must be digest-pinned"),
        ("no-source", "needs manifest or images"),
    ],
)
def test_bad_fixture_reports_expected_problem(case, substring):
    root = FIXTURES / "bad" / case
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any(substring in p for p in problems), problems


def test_errors_within_one_campaign_are_all_collected_not_just_first():
    root = FIXTURES / "bad" / "multi-error"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any("unsafe volume id" in p for p in problems)
    assert any("duplicate volume id" in p for p in problems)


def test_errors_across_files_are_all_collected_a_broken_file_does_not_hide_others():
    root = FIXTURES / "bad" / "multi-file"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any("unsafe volume id" in p for p in problems)
    assert any("duplicate volume id" in p for p in problems)


def test_missing_converter_yaml_falls_back_to_defaults():
    root = FIXTURES / "bad" / "unsafe-volume-id"
    # this fixture has no converter.yaml; the campaign is still broken, but
    # the config must not itself become a problem.
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert not any("converter.yaml" in p for p in exc_info.value.problems)


def test_volume_source_line_manifest_shape():
    v = Volume(id="R1", manifest="https://example.org/m")
    assert v.source_line() == "R1\thttps://example.org/m"


def test_volume_source_line_images_shape():
    v = Volume(
        id="R1", images=["https://example.org/a.jpg", "https://example.org/b.jpg"]
    )
    assert (
        v.source_line()
        == "R1\timages:https://example.org/a.jpg,https://example.org/b.jpg"
    )
