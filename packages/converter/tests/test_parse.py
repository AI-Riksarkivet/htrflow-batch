import json
import shutil
from pathlib import Path

import pytest

from htrflow_converter.models import ConverterConfig, Volume
from htrflow_converter.parse import ValidationError, load

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"


def _load(root: Path):
    return load(root / "campaigns", root / "pipelines", root / "converter.yaml")


def _setting(path: Path, line: str) -> None:
    """Append ``line`` to a YAML file, dropping the top-level line that sets
    the same key first: a key written twice is its own problem (C-6)."""
    key = line.partition(":")[0].strip()
    kept = [
        kept for kept in path.read_text().splitlines() if kept.partition(":")[0] != key
    ]
    path.write_text("\n".join(kept) + f"\n{line}\n")


def test_good_fixture_loads_campaigns_and_pipelines():
    campaigns, pipelines, cfg = _load(GOOD)
    assert len(campaigns) == 2
    assert len(pipelines) == 1
    assert "demo-v1" in pipelines
    assert cfg.namespace == "htr-test"
    assert cfg.window == 10
    # A step naming a model with no revision: Kyverno's rule now (the
    # chart's `security.requireModelRevision`), never the converter's.
    steps = pipelines["demo-v1"].steps
    assert any(s.get("settings", {}).get("model_settings") for s in steps)


def test_good_fixture_bare_id_expands_with_source_template():
    campaigns, _, cfg = _load(GOOD)
    kyrk = next(c for c in campaigns if c.name == "kyrk")
    v = {v.id: v for v in kyrk.volumes}
    assert v["R0001203"].manifest == cfg.source_template.format(ref="R0001203")
    assert v["R0001203"].images == []


def test_source_template_has_no_default():
    """No archive's IIIF host is built in: a repo that writes bare reference
    codes names its own (audit 0923 ruling 1)."""
    assert ConverterConfig().source_template == ""


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
    assert loc.priority == "htr-interactive"
    assert loc.window == 5


#: Every message a campaign author can be shown by a broken fixture, pinned
#: verbatim: the wording IS the feature (B63 Task 20G, "a lot more human
#: friendly"), so changing one is a deliberate edit of this table and not
#: something a refactor can do quietly. Each is
#: `path/to/file.yaml: <what is wrong> — <what to do about it>`.
EXPECTED = {
    # One sentence per campaign, never one per volume: a campaign of ten
    # thousand bare codes is one fix, in converter.yaml (audit 0923 ruling 1).
    "bare-ref-no-template": [
        'campaigns/broken.yaml: volumes "R1", "R2", "R3" and 1 more are bare '
        "reference codes, and converter.yaml has no source_template to turn "
        "them into manifest URLs — set source_template in converter.yaml (e.g. "
        '"https://iiif.example.org/{ref}/manifest"), or write each volume as '
        '"id:" with "manifest: <url>"',
        'campaigns/second.yaml: volume "R9" is a bare reference code, and '
        "converter.yaml has no source_template to turn it into a manifest URL "
        "— set source_template in converter.yaml (e.g. "
        '"https://iiif.example.org/{ref}/manifest"), or write the volume as '
        '"id:" with "manifest: <url>"',
    ],
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
        'converter.yaml: "window" must be a whole number (got "not-an-int")',
        'converter.yaml: "bogus_field" is not a setting this file has — '
        "remove it, or fix the spelling",
    ],
    "moved-allowed-image-repos": [
        "converter.yaml: allowed_image_repos moved to the htrflow-batch chart "
        "(security.allowedImageRepos, enforced by Kyverno) — remove it from "
        "converter.yaml",
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
    "moved-require-model-revision": [
        "converter.yaml: require_model_revision moved to the htrflow-batch "
        "chart (security.requireModelRevision, enforced by Kyverno) — remove "
        "it from converter.yaml",
    ],
    "window": [
        'campaigns/a.yaml: "window" must be a whole number of 1 or more (got '
        '"not-a-number")',
        'campaigns/b.yaml: volume "R1" is listed twice — remove the duplicate',
    ],
    "part-name": [
        "campaigns/foo-part1.yaml: the campaign name (taken from the file "
        'name) ends in "-part<number>", which is what the converter calls the '
        "parts of a campaign it splits — rename the file",
    ],
    "status-name": [
        "campaigns/kyrk-status.yaml: the campaign name (taken from the file "
        'name) ends in "-status", which is what the read API calls the '
        "ConfigMap it writes beside the record of a campaign — rename the file",
    ],
    "bad-suspend": [
        'campaigns/broken.yaml: "suspend" must be true or false (got "maybe")',
        'campaigns/broken2.yaml: "suspend" must be true or false (got a list)',
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


@pytest.mark.parametrize(
    "line,expected",
    [
        # The audited set of pydantic error types (parse._TYPE_SENTENCES), one
        # value per type an author can actually reach. None of these may show
        # "Input should be ..." or a bare list index.
        ("window: 0", '"window" must be 1 or more (got 0)'),
        ("window: not-an-int", '"window" must be a whole number (got "not-an-int")'),
        ("window: 1.5", '"window" must be a whole number (got 1.5)'),
        ("window: {a: 1}", '"window" must be a whole number (got a block of settings)'),
        ("window: [1]", '"window" must be a whole number (got a list)'),
        ("namespace: [1]", '"namespace" must be text (got a list)'),
        # `bool_type`/`bool_parsing` are not reachable from converter.yaml
        # since its only bool (`require_model_revision`) moved to the chart;
        # the `bad-suspend` fixture pins those two sentences instead --
        # broken.yaml ("maybe") for `bool_parsing`, broken2.yaml (`[1]`)
        # for `bool_type`.
        ("tolerations: 3", '"tolerations" must be a list of entries (got 3)'),
        (
            "tolerations: [3]",
            '"tolerations" entry 1 must be settings written as "key: value" '
            "lines (got 3)",
        ),
        (
            "node_selector: 3",
            '"node_selector" must be settings written as "key: value" lines (got 3)',
        ),
        ("node_selector: {a: 1}", '"node_selector.a" must be text (got 1)'),
        (
            "nope: 1",
            '"nope" is not a setting this file has — remove it, or fix the spelling',
        ),
    ],
)
def test_every_pydantic_error_type_reads_as_a_sentence(tmp_path, line, expected):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, line)
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [f"converter.yaml: {expected}"]


def test_missing_converter_yaml_falls_back_to_defaults():
    root = FIXTURES / "bad" / "unsafe-volume-id"
    # this fixture has no converter.yaml; the campaign is still broken, but
    # the config must not itself become a problem.
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert not any("converter.yaml" in p for p in exc_info.value.problems)


@pytest.mark.parametrize(
    "key,chart_key",
    [
        ("allowed_image_repos:\n  - ghcr.io/riksarkivet", "security.allowedImageRepos"),
        ("require_model_revision: true", "security.requireModelRevision"),
    ],
)
def test_a_policy_key_left_in_converter_yaml_points_at_the_chart(
    tmp_path, key, chart_key
):
    """Task 22: both rules are Kyverno ClusterPolicies the chart ships, so
    converter.yaml no longer has these keys. `extra="forbid"` would reject
    them as a spelling mistake ("is not a setting this file has"), which
    would send their author looking for the typo instead of at the chart."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, key)
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        f"converter.yaml: {key.split(':')[0]} moved to the htrflow-batch chart "
        f"({chart_key}, enforced by Kyverno) — remove it from converter.yaml"
    ]


def test_both_moved_policy_keys_left_in_converter_yaml_are_one_problem(tmp_path):
    """Fix round 2 #3: both obsolete keys in the same converter.yaml must not
    cost two rounds — `_reject_moved_settings` used to raise on the first
    hit it found, so a second offender only surfaced after the first was
    fixed and the file re-run."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    cfg.write_text(
        cfg.read_text()
        + "\nallowed_image_repos:\n  - ghcr.io/riksarkivet\n"
        + "require_model_revision: true\n"
    )
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        "converter.yaml: allowed_image_repos moved to the htrflow-batch "
        "chart (security.allowedImageRepos, enforced by Kyverno); "
        "require_model_revision moved to the htrflow-batch chart "
        "(security.requireModelRevision, enforced by Kyverno) — "
        "remove them from converter.yaml"
    ]


def test_a_stale_pipeline_model_revision_is_one_line(tmp_path):
    """Fix round 2 #4: `Pipeline.model_revision` was deleted (nothing read
    it — B63 Task 22 moved the revision rule to Kyverno's own field path).
    `Pipeline` now forbids unknown keys, so a pipeline file that still
    carries `model_revision:` gets the same one-line sentence as any other
    typo instead of being silently ignored."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    pipeline = root / "pipelines" / "demo-v1.yaml"
    pipeline.write_text(pipeline.read_text() + "\nmodel_revision: deadbeef\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        'pipelines/demo-v1.yaml: "model_revision" is not a setting this '
        "file has — remove it, or fix the spelling"
    ]


def test_volume_source_line_manifest_shape():
    v = Volume(id="R1", manifest="https://example.org/m")
    assert v.source_line() == "R1\thttps://example.org/m"


def test_volume_source_line_images_shape():
    v = Volume(
        id="R1", images=["https://example.org/a.jpg", "https://example.org/b.jpg"]
    )
    assert (
        v.source_line()
        == "R1\timages:https://example.org/a.jpg https://example.org/b.jpg"
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
    _setting(cfg, f"{key}: 0")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert any(f'converter.yaml: "{key}"' in p for p in exc_info.value.problems), (
        exc_info.value.problems
    )


def test_hf_token_secret_defaults_to_unset_and_takes_a_secret_name(tmp_path):
    """A private (or gated) Hub model is only reachable by the warm-up, and
    only if the operator made a Secret for it. Unset is the normal case."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    assert _load(root)[2].hf_token_secret == ""
    cfg = root / "converter.yaml"
    _setting(cfg, "hf_token_secret: htr-batch-hf")
    assert _load(root)[2].hf_token_secret == "htr-batch-hf"


def test_hf_token_secret_must_be_a_secret_name(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, "hf_token_secret: Not_A_Secret")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert any(
        'converter.yaml: "hf_token_secret"' in p and "Secret" in p
        for p in exc_info.value.problems
    ), exc_info.value.problems


@pytest.mark.parametrize(
    "template",
    [
        "https://iiif.example.org/{id}/manifest",  # the wrong placeholder
        "https://iiif.example.org/manifest",  # none at all
        "https://iiif.example.org/{ref}/{ref}/manifest",  # twice
        "https://iiif.example.org/{ref/manifest",  # a brace left open
    ],
)
def test_a_source_template_that_cannot_be_filled_is_one_sentence(tmp_path, template):
    """`source_template.format(ref=...)` runs inside a validator, where a
    stray placeholder left pydantic with a KeyError/IndexError and the author
    with a traceback. The template is checked where it is written instead."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, f'source_template: "{template}"')
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith('converter.yaml: "source_template" ')
    assert "{ref}" in problem


@pytest.mark.parametrize(
    "bad", ["high priority", "hög-prio", "p" * 64, "-high", "HTR-Bulk", "htr.bulk"]
)
def test_campaign_priority_must_be_a_class_name(tmp_path, bad):
    """`priority:` is rendered straight into the `kueue.x-k8s.io/priority-class`
    LABEL and has to name a WorkloadPriorityClass, whose names are DNS
    labels. A case slip like `HTR-Bulk` is a legal label value that names
    no class, and Kueue does not refuse that: the Job stays suspended with
    no event. So the alphabet is the class one, refused in `validate`."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    campaign = root / "campaigns" / "kyrk.yaml"
    campaign.write_text(campaign.read_text() + f'\npriority: "{bad}"\n')
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith('campaigns/kyrk.yaml: "priority" ')
    assert "kueue.x-k8s.io/priority-class" in problem


def test_a_priority_the_cluster_has_no_class_for_is_refused(tmp_path):
    """Verified on a live Kueue: `ValidateJobOnCreate` never looks at the
    label, the reconciler fails to extract the priority, creates no Workload
    and raises no event -- the Job stays suspended and the browser shows
    "Queued" for ever. `converter.yaml`'s `priority_classes` is the cluster
    fact `validate` checks instead, and the sentence names the campaign
    file, the classes there are, and where the list comes from."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    campaign = root / "campaigns" / "kyrk.yaml"
    campaign.write_text(campaign.read_text() + "\npriority: htr-urgent\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem == (
        'campaigns/kyrk.yaml: priority "htr-urgent" is not one of the '
        "cluster's classes (htr-interactive, htr-bulk, htr-idle) — set "
        "converter.yaml priority_classes to what the chart's "
        "queue.priorityClasses ships"
    )


def test_an_empty_class_list_refuses_every_priority(tmp_path):
    """A cluster whose chart renders no class offers no priority at all: the
    fixture's `loc` campaign, which names one, is the one refused."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, "priority_classes: []")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith('campaigns/loc.yaml: priority "htr-interactive" ')
    assert "(none)" in problem


@pytest.mark.parametrize(
    "line,said",
    [
        (
            "priority_classes: [htr-interactive, HTR-Bulk]",
            "is not a priority class name",
        ),
        ("priority_classes: [htr-bulk, htr-bulk]", "lists the same class twice"),
    ],
)
def test_the_class_list_itself_is_held_to_the_chart_schema(tmp_path, line, said):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, line)
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith('converter.yaml: "priority_classes" ')
    assert said in problem


@pytest.mark.parametrize(
    "key,bad",
    [
        ("queue", "htr batch"),
        ("s3_secret", "htr_batch_s3"),
        ("data_pvc", "-htr-test-data"),
        ("runtime_class", "NVIDIA"),
    ],
)
def test_a_converter_yaml_object_name_must_be_a_kubernetes_name(tmp_path, key, bad):
    """Every one of these names an object the API server has to accept. A
    capitalised namespace passed `validate` and was refused at apply time,
    which is after the render is committed and the campaign is live."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, f'{key}: "{bad}"')
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith(
        f'converter.yaml: "{key}" is not a Kubernetes object name'
    )


@pytest.mark.parametrize(
    "line",
    [
        'node_selector: {"Nvidia Com/gpu": "present"}',
        'node_selector: {"nvidia.com/gpu.present": "yes please"}',
    ],
)
def test_a_node_selector_that_is_not_a_label_is_refused(tmp_path, line):
    """`node_selector` is copied into the pod spec as `nodeSelector`, where
    both halves have to be a label -- a pod the API server will not take is a
    campaign that never starts."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, line)
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith('converter.yaml: "node_selector" ')
    assert "node label" in problem


@pytest.mark.parametrize("key", ["manifest_max_bytes", "fetch_max_bytes"])
def test_a_byte_cap_of_zero_or_less_is_refused(tmp_path, key):
    """Both are handed to the wrapper as the largest thing it may fetch. At
    0 every manifest and every image is over the cap, so every volume in
    every campaign fails -- and nothing said so at render time."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, f"{key}: 0")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        f'converter.yaml: "{key}" must be 1 or more (got 0)'
    ]


@pytest.mark.parametrize("key", ["max_seconds", "ttl_seconds_after_finished"])
def test_seconds_beyond_a_32_bit_field_are_refused(tmp_path, key):
    """Both are rendered into 32-bit Kubernetes fields
    (`activeDeadlineSeconds`, `ttlSecondsAfterFinished`); a larger number is
    refused by the API server halfway through an apply."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, f"{key}: 4294967296")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert (
        problem
        == f'converter.yaml: "{key}" must be 2147483647 or less (got 4294967296)'
    )


def test_a_pipelines_seconds_are_capped_the_same_way(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    pipeline = root / "pipelines" / "demo-v1.yaml"
    pipeline.write_text(pipeline.read_text() + "\nmax_seconds: 4294967296\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem == (
        'pipelines/demo-v1.yaml: "max_seconds" must be 2147483647 or less '
        "(got 4294967296)"
    )


def test_a_campaign_with_no_volumes_at_all_is_refused(tmp_path):
    """No volumes renders a Job with `completions: 0`, which Kubernetes
    reports as Succeeded the moment it is created: a campaign that is over
    before it starts, and a green one at that."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "campaigns" / "kyrk.yaml").write_text("pipeline: demo-v1\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith("campaigns/kyrk.yaml: this campaign lists no volumes")


@pytest.mark.parametrize("bad", ["htr.batch.example", "h" * 64, "HTR-Batch", ""])
def test_a_namespace_is_a_label_not_a_subdomain(tmp_path, bad):
    """A namespace is a DNS-1123 *label*: no dots, at most 63 characters. The
    wider object-name rule let both through `validate` and left them to the
    API server, which refuses them once the render is already committed."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    cfg = root / "converter.yaml"
    _setting(cfg, f'namespace: "{bad}"')
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    (problem,) = exc_info.value.problems
    assert problem.startswith(
        'converter.yaml: "namespace" is not a Kubernetes namespace'
    )
    assert "63" in problem


@pytest.mark.parametrize(
    "line,key",
    [("suspended: true", "suspended"), ("priorty: htr-idle", "priorty")],
)
def test_a_misspelt_campaign_setting_is_refused_not_dropped(tmp_path, line, key):
    """audit 0923 C-1: a pause or a priority under a misspelt key was dropped
    without a word, and the campaign rendered -- and ran -- at the defaults."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    campaign = root / "campaigns" / "kyrk.yaml"
    campaign.write_text(campaign.read_text() + f"\n{line}\n")
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        f'campaigns/kyrk.yaml: "{key}" is not a setting this file has — '
        "remove it, or fix the spelling"
    ]


def test_a_stray_volume_setting_is_refused_not_dropped(tmp_path):
    """audit 0923 C-1: `pages: 1-10` on a volume read as a page range to its
    author and as nothing to the converter, which ran every page."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "campaigns" / "kyrk.yaml").write_text(
        "pipeline: demo-v1\n"
        "volumes:\n"
        "  - id: R1\n"
        "    manifest: https://iiif.example.org/r1/manifest\n"
        "    pages: 1-10\n"
    )
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        'campaigns/kyrk.yaml: volume 1 has "pages", which is not a setting a '
        "volume has — remove it, or fix the spelling"
    ]


@pytest.mark.parametrize(
    "entry,kind",
    [
        ("- id: 0012345\n    manifest: https://x.example/m", "a number (5349)"),
        ("- id: 1:20\n    manifest: https://x.example/m", "a number (80)"),
        ("- id: 1.10\n    manifest: https://x.example/m", "a number (1.1)"),
        ("- id: 12_000\n    manifest: https://x.example/m", "a number (12000)"),
        ("- id: 0x1F\n    manifest: https://x.example/m", "a number (31)"),
        ("- id: yes\n    manifest: https://x.example/m", "true or false (true)"),
        ("- id: 2024-01-31\n    manifest: https://x.example/m", "a date (2024-01-31)"),
        ("- id:\n    manifest: https://x.example/m", "nothing at all"),
        ("- 0012345", "a number (5349)"),
        ("- 1.10", "a number (1.1)"),
        ("- yes", "true or false (true)"),
    ],
)
def test_a_volume_id_yaml_reads_as_something_else_is_refused(tmp_path, entry, kind):
    """audit 0923 C-5: a mapping id went through YAML 1.1's number rules and
    then str() -- `0012345` became volume `5349`, `1:20` became `80` -- and
    the bare form said the entry "has no id". Both forms need a string."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "campaigns" / "kyrk.yaml").write_text(
        f"pipeline: demo-v1\nvolumes:\n  {entry}\n"
    )
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        f"campaigns/kyrk.yaml: volume 1 has an id that YAML reads as {kind}, "
        'not as text — put it in quotes so it stays as written: - "R0012345", '
        'or id: "R0012345"'
    ]


def test_a_quoted_numeric_volume_id_stays_as_written(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "campaigns" / "kyrk.yaml").write_text(
        'pipeline: demo-v1\nvolumes:\n  - "0012345"\n'
        '  - id: "1.10"\n    manifest: https://x.example/m\n'
    )
    campaigns, _, _ = _load(root)
    kyrk = next(c for c in campaigns if c.name == "kyrk")
    assert [v.id for v in kyrk.volumes] == ["0012345", "1.10"]


@pytest.mark.parametrize(
    "rel,text,key,lines",
    [
        (
            "campaigns/kyrk.yaml",
            "pipeline: demo-v1\nvolumes: [R1]\nvolumes: [R2]\n",
            "volumes",
            (2, 3),
        ),
        (
            "pipelines/demo-v1.yaml",
            "image: ghcr.io/x/y@sha256:" + "a" * 64 + "\n"
            "steps:\n"
            "  - step: Segmentation\n"
            "    settings:\n"
            "      model: yolo\n"
            "      model: TrOCR\n",
            "model",
            (5, 6),
        ),
        (
            "converter.yaml",
            "namespace: htr-test\nwindow: 10\nqueue: a\nwindow: 2\n",
            "window",
            (2, 4),
        ),
    ],
)
def test_a_key_written_twice_is_refused_not_last_wins(tmp_path, rel, text, key, lines):
    """audit 0923 C-6: YAML's own loader keeps the last of two equal keys, so
    `volumes: [R1]` and then `volumes: [R2]` rendered R2 alone."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / rel).write_text(text)
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        f'{rel}: "{key}" is written twice, on lines {lines[0]} and {lines[1]} — '
        "YAML would keep only the last one, so remove one or merge them"
    ]


def test_two_merge_keys_in_one_mapping_are_a_key_written_twice(tmp_path):
    """`<<:` twice is two keys to YAML, and the second merge would decide
    alone what the first one said."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "campaigns" / "kyrk.yaml").write_text(
        "a: &a {pipeline: demo-v1}\nb: &b {volumes: [R1]}\nc:\n  <<: *a\n  <<: *b\n"
    )
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [
        'campaigns/kyrk.yaml: "<<" is written twice, on lines 4 and 5 — YAML '
        "would keep only the last one, so remove one or merge them"
    ]


def test_a_merge_key_may_still_override_what_it_merges(tmp_path):
    """`<<:` is how a YAML file says "these, except"; the key it overrides
    is not written twice."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "campaigns" / "kyrk.yaml").write_text(
        "base: &b {manifest: https://x.example/m}\n"
        "pipeline: demo-v1\n"
        "volumes:\n"
        "  - {<<: *b, id: R1, manifest: https://x.example/n}\n"
    )
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    # Only the unknown `base:` key, which is the anchor's carrier.
    assert exc_info.value.problems == [
        'campaigns/kyrk.yaml: "base" is not a setting this file has — remove '
        "it, or fix the spelling"
    ]


@pytest.mark.parametrize(
    "steps,said",
    [
        (
            "steps: []\n",
            '"steps" is empty — a pipeline runs at least one step; write steps: '
            'and then "- step: <Name>" entries under it',
        ),
        (
            "steps:\n  - step: [x]\n",
            '"steps" has a step whose "step:" is not a name (step 1) — every '
            'entry starts "- step: <Name>", the htrflow step it runs',
        ),
        (
            "steps:\n  - settings: {model: yolo}\n  - step: TextRecognition\n",
            '"steps" has a step with no "step:" name (step 1) — every entry '
            'starts "- step: <Name>", the htrflow step it runs',
        ),
    ],
)
def test_a_pipeline_with_no_step_to_run_is_refused(tmp_path, steps, said):
    """audit 0923 C-8: both rendered, and the first a pod reached was the
    wrapper failing every volume of every campaign on the pipeline."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "pipelines" / "demo-v1.yaml").write_text(
        "image: ghcr.io/x/y@sha256:" + "a" * 64 + "\n" + steps
    )
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [f"pipelines/demo-v1.yaml: {said}"]


_KEYLESS = (
    'converter.yaml: "tolerations" entry 1 has no key — a toleration without '
    "one tolerates every taint on every node, the control plane's included; "
    "name the taint it is for"
)


@pytest.mark.parametrize(
    "line,said",
    [
        ("tolerations: [{operator: Exists}]", _KEYLESS),
        ('tolerations: [{key: "", operator: Exists}]', _KEYLESS),
        ("tolerations: [{effect: NoSchedule, operator: Exists}]", _KEYLESS),
        (
            "tolerations: [{key: node-role.kubernetes.io/control-plane, "
            "operator: Exists}]",
            'converter.yaml: "tolerations" entry 1 tolerates '
            "node-role.kubernetes.io/control-plane — that taint keeps "
            "workloads off the control plane, and a GPU batch pod has no "
            "business there; remove it",
        ),
        (
            "tolerations: [{key: gpu, operator: Exists, value: x}]",
            'converter.yaml: "tolerations" entry 1 has a value with operator: '
            "Exists, which matches the key alone — drop the value, or use "
            "operator: Equal",
        ),
        (
            "tolerations: [{key: gpu, tolerationSeconds: 60}]",
            'converter.yaml: "tolerations" entry 1 has tolerationSeconds '
            "without effect: NoExecute, the only effect it applies to",
        ),
        (
            "tolerations: [{key: gpu, opertor: Exists}]",
            'converter.yaml: "tolerations.opertor" entry 1 is not a setting '
            "this file has — remove it, or fix the spelling",
        ),
        (
            "tolerations: [{key: gpu, operator: exists}]",
            'converter.yaml: "tolerations.operator" entry 1 must be one of '
            'Exists or Equal (got "exists")',
        ),
        (
            "tolerations: [{key: gpu, effect: NoRun}]",
            'converter.yaml: "tolerations.effect" entry 1 must be one of '
            'NoSchedule, PreferNoSchedule or NoExecute (got "NoRun")',
        ),
        (
            'tolerations: [{key: "bad key"}]',
            'converter.yaml: "tolerations" entry 1 has a key that is not a '
            'Kubernetes taint key (got "bad key") — a key is a name, '
            'optionally after a "<dns-prefix>/"; letters, digits, ".", "_" '
            'and "-", at most 63 characters',
        ),
    ],
)
def test_a_toleration_is_held_to_the_kubernetes_shape(tmp_path, line, said):
    """audit 0923 S-1: `tolerations` was `list[dict]`, copied into every pod
    as written: `{operator: Exists}` tolerates every taint, so a campaign's
    GPU pods could land on the control plane or any node tainted to keep
    them off."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    _setting(root / "converter.yaml", line)
    with pytest.raises(ValidationError) as exc_info:
        _load(root)
    assert exc_info.value.problems == [said]


def test_a_well_formed_toleration_is_kept_as_written(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    _setting(
        root / "converter.yaml",
        "tolerations: [{key: nvidia.com/gpu, operator: Exists, effect: NoSchedule},"
        " {key: dedicated, value: htr, effect: NoExecute, tolerationSeconds: 30}]",
    )
    _, _, cfg = _load(root)
    assert [t.manifest() for t in cfg.tolerations] == [
        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"},
        {"key": "dedicated", "value": "htr", "effect": "NoExecute",
         "tolerationSeconds": 30},
    ]  # fmt: skip


@pytest.mark.parametrize(
    "url,why",
    [
        ("https://example.org:99999/m", "its port is not a number from 0 to 65535"),
        ("https://exa%mple.org/m", "its host is not a host name or an IP address"),
        ("https://ex<ample.org/m", "its host is not a host name or an IP address"),
        ("https://example.org\\m", "it has a backslash or a control character in it"),
        (
            "https://example.org/\x01m",
            "it has a backslash or a control character in it",
        ),
        ("https://[fe80::1%25eth0]/m", "its host is not a host name or an IP address"),
        ("https://1.2.3.999/m", "its host is not a host name or an IP address"),
        ("https://a..b/m", "its host is not a host name or an IP address"),
        ("https://xn--abc/m", "its host is not a host name or an IP address"),
        (
            "https://xn--mgbh0fb.example/m",
            "its host is not a host name or an IP address",
        ),
        ("https://example.org:x/m", "its port is not a number from 0 to 65535"),
        # decodes to "Übung": a browser would have encoded "übung" instead
        (
            "https://xn--bung-fna.example/m",
            "its host is not a host name or an IP address",
        ),
        ("https://.example.org/m", "its host is not a host name or an IP address"),
        ("https://example.org../m", "its host is not a host name or an IP address"),
    ],
)
def test_a_url_a_browser_cannot_open_is_refused(tmp_path, url, why):
    """audit 0923 F-7 (the web fixer's request): these passed the converter
    and then threw in the browser's WHATWG `new URL`, which the viewer and
    the status page build every link with. The rule is a strict subset of
    WHATWG's: what passes here, a browser opens."""
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    for form, what in (
        (f"- id: R1\n    manifest: {json.dumps(url)}\n", "a manifest"),
        (f"- id: R1\n    images: [{json.dumps(url)}]\n", "an image"),
    ):
        (root / "campaigns" / "kyrk.yaml").write_text(
            f"pipeline: demo-v1\nvolumes:\n  {form}"
        )
        with pytest.raises(ValidationError) as exc_info:
            _load(root)
        (problem,) = exc_info.value.problems
        assert problem.startswith(
            f"campaigns/kyrk.yaml: volume 1 has {what} a browser cannot open ("
        ), problem
        assert problem.endswith(f"): {why}"), problem


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/m",
        "http://example.org:8080/iiif/manifest.json",
        "https://user@example.org/m?x=1#y",
        "https://[2001:db8::1]:443/m",
        "https://192.0.2.10/m",
        "https://iiif_host-1.example.org/m",
        "https://xn--rksarkivet-z5a.se/m",
        "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest",
        "https://example.org/full/2500,/0/default.jpg",
        # what browsers take and the read API's browser_http_url does too
        "https://example.org./m",
        "https://bücher.example/m",
        "https://xn--bcher-kva.example/m",
    ],
)
def test_a_url_a_browser_opens_is_kept(tmp_path, url):
    root = tmp_path / "repo"
    shutil.copytree(GOOD, root)
    (root / "campaigns" / "kyrk.yaml").write_text(
        f"pipeline: demo-v1\nvolumes:\n  - id: R1\n    manifest: {json.dumps(url)}\n"
    )
    campaigns, _, _ = _load(root)
    assert next(c for c in campaigns if c.name == "kyrk").volumes[0].manifest == url
