import shutil
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


#: Every message a campaign author can be shown by a broken fixture, pinned
#: verbatim: the wording IS the feature (B63 Task 20G, "a lot more human
#: friendly"), so changing one is a deliberate edit of this table and not
#: something a refactor can do quietly. Each is
#: `path/to/file.yaml: <what is wrong> — <what to do about it>`.
EXPECTED = {
    "unsafe-volume-id": [
        'campaigns/broken.yaml: volume 1 ("a/b") has an id with characters '
        'that are not allowed — use only letters, digits, ".", "_" and "-", '
        "at most 63 of them",
    ],
    "duplicate-volume-id": [
        'campaigns/broken.yaml: volume "R1" is listed twice — remove the duplicate',
    ],
    "bad-url": [
        "campaigns/broken.yaml: volume 1 has a manifest that is not an "
        'http(s) URL ("javascript:alert(1)") — write the whole URL, starting '
        "with https://",
    ],
    "unknown-pipeline": [
        'campaigns/broken.yaml: pipeline "does-not-exist" has no file in '
        "pipelines/ — add pipelines/does-not-exist.yaml, or point pipeline: "
        "at one that is there",
    ],
    "bad-image": [
        'pipelines/demo-v1.yaml: "image" is not pinned to a digest (got '
        '"repo/img:v5") — write image: <registry>/<repo>@sha256:<64 hex '
        "digits>",
    ],
    "no-source": [
        "campaigns/broken.yaml: volume 1 needs exactly one source — give it "
        "manifest: <IIIF manifest URL>, or images: <list of image URLs>",
    ],
    "converter-two-errors": [
        'converter.yaml: "window" must be a whole number (got "not-an-int" '
        "— quotes make it text)",
        'converter.yaml: "bogus_field" is not a setting this file has — '
        "remove it, or fix the spelling",
    ],
    "image-repo-sibling": [
        "pipelines/demo-v1.yaml: the image is not from an allowed repository "
        '("ghcr.io/riksarkivet-evil/x@sha256:' + "a" * 64 + '") — '
        "converter.yaml allows only: ghcr.io/riksarkivet",
    ],
    "multi-error": [
        'campaigns/broken.yaml: volume "R1" is listed twice — remove the duplicate',
        'campaigns/broken.yaml: volume 1 ("a/b") has an id with characters '
        'that are not allowed — use only letters, digits, ".", "_" and "-", '
        "at most 63 of them",
    ],
    "multi-file": [
        'campaigns/a.yaml: volume 1 ("a/b") has an id with characters that '
        'are not allowed — use only letters, digits, ".", "_" and "-", at '
        "most 63 of them",
        'campaigns/b.yaml: volume "R1" is listed twice — remove the duplicate',
    ],
    "require-model-revision": [
        'pipelines/demo-v1.yaml: the model "Riksarkivet/yolov9-regions-1" is '
        "not pinned to a revision — converter.yaml sets "
        "require_model_revision, so add revision: <40-character commit hash>",
    ],
    "window": [
        'campaigns/a.yaml: "window" must be a whole number of 1 or more (got '
        '"not-a-number" — quotes make it text)',
        'campaigns/b.yaml: volume "R1" is listed twice — remove the duplicate',
    ],
}


def test_every_bad_fixture_has_a_pinned_message():
    """A new fixture without an expected sentence would otherwise be silently
    unpinned, which is how wording drifts back into machine-speak."""
    cases = {p.name for p in (FIXTURES / "bad").iterdir() if p.is_dir()}
    assert cases == set(EXPECTED)


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_bad_fixture_reports_expected_problem(case):
    with pytest.raises(ValidationError) as exc_info:
        _load(FIXTURES / "bad" / case)
    assert exc_info.value.problems == EXPECTED[case]


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_no_problem_leaks_a_python_repr_or_a_loc_path(case):
    """The rule for every surface (B63 Task 20G): no reprs, no internal
    names, no `volumes.0.id` paths -- a campaign author reads YAML, not
    pydantic."""
    with pytest.raises(ValidationError) as exc_info:
        _load(FIXTURES / "bad" / case)
    for problem in exc_info.value.problems:
        assert "volumes." not in problem, problem
        assert "'" not in problem, problem  # a repr's quotes
        assert "Input should be" not in problem, problem
        assert "Field required" not in problem, problem


@pytest.mark.parametrize(
    "case,summary",
    [
        ("unsafe-volume-id", "1 problem in 1 file"),
        ("converter-two-errors", "2 problems in 1 file"),
        ("multi-file", "2 problems in 2 files"),
    ],
)
def test_summary_counts_problems_and_files(case, summary):
    with pytest.raises(ValidationError) as exc_info:
        _load(FIXTURES / "bad" / case)
    assert exc_info.value.summary == summary


def test_errors_within_one_campaign_are_all_collected_not_just_first():
    root = FIXTURES / "bad" / "multi-error"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any("has an id with characters that are not allowed" in p for p in problems)
    assert any("is listed twice" in p for p in problems)


def test_errors_across_files_are_all_collected_a_broken_file_does_not_hide_others():
    root = FIXTURES / "bad" / "multi-file"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any("has an id with characters that are not allowed" in p for p in problems)
    assert any("is listed twice" in p for p in problems)


def test_missing_converter_yaml_falls_back_to_defaults():
    root = FIXTURES / "bad" / "unsafe-volume-id"
    # this fixture has no converter.yaml; the campaign is still broken, but
    # the config must not itself become a problem.
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert not any("converter.yaml" in p for p in exc_info.value.problems)


def test_bad_window_reports_message_and_does_not_abort_other_files():
    """Fix round 1 #1: a non-numeric window: must not crash load() (it used
    to raise ValueError uncaught) and the second campaign's own problem in
    the same repo must still be reported."""
    root = FIXTURES / "bad" / "window"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any(
        "must be a whole number of 1 or more" in p and "not-a-number" in p
        for p in problems
    ), problems
    assert any("is listed twice" in p for p in problems), problems


def test_allowed_image_repos_rejects_sibling_prefix():
    """Fix round 1 #2: ghcr.io/riksarkivet-evil must not be admitted by an
    allow-list entry of ghcr.io/riksarkivet (path-boundary match)."""
    root = FIXTURES / "bad" / "image-repo-sibling"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any("is not from an allowed repository" in p for p in problems), problems


def test_allowed_image_repos_accepts_legitimate_repo():
    root = GOOD / "allowed-repo"
    campaigns, pipelines, cfg = _load(root)
    assert cfg.allowed_image_repos == ["ghcr.io/riksarkivet"]
    assert pipelines["demo-v1"].image.startswith("ghcr.io/riksarkivet/")


def test_converter_yaml_errors_are_one_problem_per_field():
    """Fix round 1 #3: two bad fields in converter.yaml must surface as two
    separate problems, not one multi-line pydantic error string."""
    root = FIXTURES / "bad" / "converter-two-errors"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    converter_problems = [
        p for p in exc_info.value.problems if p.startswith("converter.yaml:")
    ]
    assert len(converter_problems) == 2, converter_problems
    assert all("\n" not in p for p in converter_problems)
    assert any("window" in p for p in converter_problems)
    assert any("bogus_field" in p for p in converter_problems)


def test_require_model_revision_true_flags_step_missing_revision():
    """Ruling: require_model_revision walks every step's model_settings,
    not a single top-level Pipeline.model_revision."""
    root = FIXTURES / "bad" / "require-model-revision"
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    problems = exc_info.value.problems
    assert any("is not pinned to a revision" in p for p in problems), problems


def test_require_model_revision_false_does_not_flag_missing_revision():
    # the good fixture's demo-v1.yaml has a step with model_settings.model
    # set and no revision; require_model_revision defaults to False there.
    campaigns, pipelines, cfg = _load(GOOD)
    assert cfg.require_model_revision is False
    assert pipelines["demo-v1"].model_revision == ""


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


def test_pipeline_max_seconds_parses_and_defaults_to_none(tmp_path):
    """Fix round 1 #3: `max_seconds:` on a pipeline overrides converter.yaml's
    global for that recipe's campaigns — a 60-page spread and a single page do
    not want the same wall-clock budget."""
    _, pipelines, _ = _load(GOOD)
    assert pipelines["demo-v1"].max_seconds is None

    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    pipeline = root / "pipelines" / "demo-v1.yaml"
    pipeline.write_text(pipeline.read_text() + "\nmax_seconds: 60\n")
    _, pipelines, _ = _load(root)
    assert pipelines["demo-v1"].max_seconds == 60


@pytest.mark.parametrize("bad", ["0", "-1", "not-a-number"])
def test_pipeline_max_seconds_must_be_a_positive_integer(tmp_path, bad):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    pipeline = root / "pipelines" / "demo-v1.yaml"
    pipeline.write_text(pipeline.read_text() + f"\nmax_seconds: {bad}\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert any(
        "must be a whole number of seconds, 1 or more" in p
        for p in exc_info.value.problems
    ), exc_info.value.problems


def test_campaign_suspend_defaults_false_and_parses_true(tmp_path):
    campaigns, _, _ = _load(GOOD)
    assert all(c.suspend is False for c in campaigns)

    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    campaign = root / "campaigns" / "kyrk.yaml"
    campaign.write_text(campaign.read_text() + "\nsuspend: true\n")
    campaigns, _, _ = _load(root)
    assert next(c for c in campaigns if c.name == "kyrk").suspend is True


@pytest.mark.parametrize("bad", ["0", "-1", "true", "1.5"])
def test_campaign_window_must_be_a_positive_non_bool_integer(tmp_path, bad):
    """`bool` is an `int` in Python, so `window: true` used to render
    `parallelism: 1` instead of failing validation; `window: 0` would render a
    Job that never starts a pod."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    campaign = root / "campaigns" / "kyrk.yaml"
    campaign.write_text(campaign.read_text() + f"\nwindow: {bad}\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert any(
        "must be a whole number of 1 or more" in p for p in exc_info.value.problems
    ), exc_info.value.problems


@pytest.mark.parametrize("bad", ["true", "0"])
def test_pipeline_max_seconds_rejects_bool_and_zero(tmp_path, bad):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    pipeline = root / "pipelines" / "demo-v1.yaml"
    pipeline.write_text(pipeline.read_text() + f"\nmax_seconds: {bad}\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert any(
        "must be a whole number of seconds, 1 or more" in p
        for p in exc_info.value.problems
    ), exc_info.value.problems


@pytest.mark.parametrize("key", ["window", "max_seconds"])
def test_converter_config_rejects_a_non_positive_window_or_max_seconds(tmp_path, key):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    cfg.write_text(cfg.read_text() + f"\n{key}: 0\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert any(f'converter.yaml: "{key}"' in p for p in exc_info.value.problems), (
        exc_info.value.problems
    )
