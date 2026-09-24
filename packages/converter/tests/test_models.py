"""Rules that live on the pydantic models themselves (spec §3, B63 Task 10)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from htrflow_converter import models, parse
from htrflow_converter.models import Campaign, Pipeline, Volume

GOOD = Path(__file__).parent / "fixtures" / "good"
#: What a repo whose campaigns write bare reference codes sets.
_TEMPLATE = 'source_template: "https://iiif.example.org/{ref}/manifest"\n'


def test_bare_string_volume_expands_with_source_template_from_context():
    v = Volume.model_validate(
        "R123", context={"source_template": "https://example.org/{ref}/manifest"}
    )
    assert v.id == "R123"
    assert v.manifest == "https://example.org/R123/manifest"
    assert v.images == []


@pytest.mark.parametrize("bad_window", [0, True])
def test_campaign_window_rejected_with_window_in_message(bad_window):
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate({"name": "c", "pipeline": "p", "window": bad_window})
    assert any(
        "must be a whole number of 1 or more" in str(e["msg"])
        for e in exc_info.value.errors()
    )


def test_pipeline_invalid_image_says_what_to_write_instead():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {"id": "p", "image": "repo/img:v5", "steps": [{"step": "Segmentation"}]}
        )
    assert any(
        "is not pinned to a digest" in str(e["msg"]) for e in exc_info.value.errors()
    )


def test_pipeline_steps_present_but_not_a_list_says_what_a_step_looks_like():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {"id": "p", "image": "ghcr.io/x/y@sha256:" + "a" * 64, "steps": "nope"}
        )
    assert any(
        "must be a list of steps" in str(e["msg"]) for e in exc_info.value.errors()
    )


@pytest.mark.parametrize(
    ("path", "key", "text"),
    [
        ("pipelines/demo-v1.yaml", "image", "steps:\n  - step: Segmentation\n"),
        ("pipelines/demo-v1.yaml", "steps", f"image: ghcr.io/x/y@sha256:{'a' * 64}\n"),
        ("campaigns/kyrk.yaml", "pipeline", "volumes:\n  - R1\n"),
    ],
)
def test_a_missing_required_key_is_one_sentence_naming_it(tmp_path, path, key, text):
    """What the author reads, through the loader that says it: the file,
    the key, and what to add -- not pydantic's "Field required"."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    (repo / path).write_text(text)
    with pytest.raises(parse.ValidationError) as exc_info:
        parse.load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")
    assert exc_info.value.problems == [
        f'{path}: "{key}" is missing — add "{key}:" to this file'
    ]


def test_campaign_empty_pipeline_points_at_the_pipelines_directory():
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate({"name": "c", "pipeline": ""})
    assert any(
        "name one of the files in pipelines/" in str(e["msg"])
        for e in exc_info.value.errors()
    )


def test_filename_wins_over_a_name_or_id_key_in_the_yaml(tmp_path):
    """`path.stem` is the campaign name / pipeline id, always -- a `name:` or
    `id:` key inside the YAML itself is silently ignored, matching the old
    hand-rolled parser (which never read either key)."""
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "campaigns" / "real-campaign.yaml").write_text(
        "name: bogus-name\npipeline: real-pipeline\nvolumes:\n  - R1\n"
    )
    (tmp_path / "pipelines" / "real-pipeline.yaml").write_text(
        "id: bogus-id\n"
        "image: ghcr.io/x/y@sha256:" + "a" * 64 + "\n"
        "steps:\n  - step: Segmentation\n"
    )
    (tmp_path / "converter.yaml").write_text(_TEMPLATE)
    campaigns, pipelines, _ = parse.load(
        tmp_path / "campaigns", tmp_path / "pipelines", tmp_path / "converter.yaml"
    )
    assert campaigns[0].name == "real-campaign"
    assert list(pipelines) == ["real-pipeline"]


# -- the images: separator (2026-09-14) ------------------------------------


def test_images_source_line_joins_on_a_space_so_a_iiif_size_comma_survives():
    """A IIIF Image API size segment is a legal comma inside a URL
    (`/full/2500,/0/default.jpg`), so comma can never be the separator: the
    line is `<id>\\t images:<url> <url>`, space-joined."""
    v = Volume(
        id="R0001203",
        images=[
            "https://images.example.org/archives!R0001203_00044/full/2500,/0/default.jpg",
            "https://images.example.org/archives!R0001203_00045/full/2500,/0/default.jpg",
        ],
    )
    assert v.source_line() == (
        "R0001203\timages:"
        "https://images.example.org/archives!R0001203_00044/full/2500,/0/default.jpg "
        "https://images.example.org/archives!R0001203_00045/full/2500,/0/default.jpg"
    )


@pytest.mark.parametrize("ws", [" ", "\t", "\n", "\r"])
def test_an_image_url_with_whitespace_is_refused_and_says_to_percent_encode_it(ws):
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate({"id": "v1", "images": [f"https://x/a{ws}b.jpg"]})
    assert any(
        "percent-encode a space as %20" in str(e["msg"])
        for e in exc_info.value.errors()
    )


def test_a_manifest_url_with_whitespace_is_refused_the_same_way():
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate({"id": "v1", "manifest": "https://x/a b/manifest"})
    assert any(
        "percent-encode a space as %20" in str(e["msg"])
        for e in exc_info.value.errors()
    )


def test_the_whitespace_problem_names_the_volume_and_the_entry(tmp_path):
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "campaigns" / "kyrk.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R1\n  - R2\n"
        "  - id: R3\n    images:\n      - 'https://x/a b.jpg'\n"
    )
    (tmp_path / "pipelines" / "demo-v1.yaml").write_text(
        "image: ghcr.io/x/y@sha256:" + "a" * 64 + "\nsteps:\n  - step: Segmentation\n"
    )
    (tmp_path / "converter.yaml").write_text(_TEMPLATE)
    with pytest.raises(parse.ValidationError) as exc_info:
        parse.load(
            tmp_path / "campaigns", tmp_path / "pipelines", tmp_path / "converter.yaml"
        )
    (problem,) = exc_info.value.problems
    assert problem.startswith("campaigns/kyrk.yaml: volume 3 ")
    assert "image 1" in problem and "percent-encode a space as %20" in problem


IMAGES_CASES = [
    "https://x/1.jpg https://x/2.jpg",
    "  https://x/1.jpg \t https://x/2.jpg\n",
    "https://x/1.jpg,https://x/2.jpg",
    "https://x/full/2500,/0/default.jpg",
    "https://x/full/2500,/0/default.jpg https://x/2.jpg",
    "https://x/1.jpg",
    "",
    # the documented limit of the transition rule: both packages split this
    # the same (wrong) way, which is what matters until the rule is deleted
    "https://p/fetch?src=https://a/1.jpg,https://b/2.jpg",
]


@pytest.mark.parametrize("value", IMAGES_CASES)
def test_the_converter_and_the_wrapper_split_images_identically(value):
    """The two packages share no code -- the wrapper's image must not carry
    the converter's Kubernetes client -- so the one rule they both implement
    is pinned here instead. A drift is a volume that renders one way and runs
    another."""
    config = pytest.importorskip("htrflow_batch.config")
    assert models.split_image_urls(value) == (
        config.Config.model_construct(images=value).image_urls
    )


@pytest.mark.parametrize(
    "volume",
    [
        {"id": "v1", "images": ["https://user:sekret@example.org/x y.jpg"]},
        {"id": "v1", "images": ["ftp://user:sekret@example.org/x.jpg"]},
        {"id": "v1", "manifest": "https://user:sekret@example.org/a b/manifest"},
        {"id": "v1", "manifest": "ftp://user:sekret@example.org/manifest"},
    ],
)
def test_a_url_with_credentials_in_it_is_never_echoed_back(volume):
    """A campaign file should carry no credentials, but a problem line is
    printed in CI logs and pasted into chat, so a URL that does carry them
    loses them here rather than everywhere downstream."""
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate(volume)
    (msg,) = [str(e["msg"]) for e in exc_info.value.errors()]
    assert "sekret" not in msg and "user" not in msg
    assert "***@example.org" in msg


def test_a_problem_line_never_carries_a_raw_tab_or_carriage_return(tmp_path):
    """One problem is one line: a control character out of the author's own
    YAML would otherwise split it in a CI log (and a tab is what volumes.txt
    separates the id from the source with)."""
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "campaigns" / "kyrk.yaml").write_text(
        'pipeline: demo-v1\nvolumes:\n  - id: R1\n    manifest: "https://x/a\\tb\\r\\nc"\n'
    )
    (tmp_path / "pipelines" / "demo-v1.yaml").write_text(
        "image: ghcr.io/x/y@sha256:" + "a" * 64 + "\nsteps:\n  - step: Segmentation\n"
    )
    with pytest.raises(parse.ValidationError) as exc_info:
        parse.load(
            tmp_path / "campaigns", tmp_path / "pipelines", tmp_path / "converter.yaml"
        )
    (problem,) = exc_info.value.problems
    assert not set(problem) & set("\t\r\n"), repr(problem)


def test_a_bare_volume_under_an_unfillable_template_is_a_sentence_not_a_keyerror():
    """The same rule, one layer down: `ConverterConfig` refuses a template
    that cannot be filled, and a `Volume` handed one anyway still leaves as a
    validation problem rather than as a traceback out of the validator."""
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate(
            "R123", context={"source_template": "https://example.org/{id}/manifest"}
        )
    (msg,) = [str(e["msg"]) for e in exc_info.value.errors()]
    assert "source_template" in msg and "{ref}" in msg


def test_an_images_volume_too_long_for_one_environment_entry_is_refused():
    """The Job's shell exports the line's URLs as `IMAGES`, and Linux caps a
    single environment entry at 128 KiB: past that the pod dies with
    "Argument list too long" before the wrapper starts, with nothing in the
    campaign to say which volume did it. Refused where the author can still
    split it."""
    urls = [f"https://example.org/scan{n:05d}.jpg" for n in range(4000)]
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate({"id": "R1", "images": urls})
    (msg,) = [str(e["msg"]) for e in exc_info.value.errors()]
    assert "4000 images" in msg
    assert "KiB" in msg and "manifest" in msg


def test_an_images_volume_just_under_the_line_budget_is_kept():
    urls = [f"https://example.org/scan{n:05d}.jpg" for n in range(3000)]
    assert len(Volume.model_validate({"id": "R1", "images": urls}).images) == 3000


@pytest.mark.parametrize(
    "param",
    [
        "X-Amz-Signature",
        "X-Amz-Security-Token",
        "X-Amz-Credential",
        "token",
        "sig",
        "signature",
        "key",
    ],
)
def test_a_signed_url_loses_its_signature_when_a_problem_echoes_it(param):
    """A problem line is printed in CI logs and pasted into chat. A presigned
    source URL carries its credential in the query, so the echo drops it the
    way it already drops userinfo -- which is all this can do: the URL itself
    is stored verbatim in volumes.txt and in `rendered/`."""
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate(
            {"id": "v1", "manifest": f"https://example.org/m anifest?{param}=sekret"}
        )
    (msg,) = [str(e["msg"]) for e in exc_info.value.errors()]
    assert "sekret" not in msg
    assert f"{param}=***" in msg


def test_a_query_parameter_that_merely_ends_in_a_redacted_name_is_left_alone():
    """`?pagekey=` and `?sig` are not the same word: the redaction is on a
    parameter, not on a substring, or half the problem lines in the repo
    would come back as asterisks."""
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate(
            {"id": "v1", "manifest": "https://example.org/m anifest?pagekey=7"}
        )
    (msg,) = [str(e["msg"]) for e in exc_info.value.errors()]
    assert "pagekey=7" in msg


_REVISION = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize(
    "settings,stray",
    [
        (
            {
                "model": "yolo",
                "model_settings": {"model": "o/yolo", "revision": _REVISION},
                "revision": None,
            },
            "revision",
        ),
        (
            {
                "model": "TrOCR",
                "model_settings": {
                    "model": "o/trocr",
                    "model_kwargs": {"revision": _REVISION},
                },
                "model_kwargs": {},
            },
            "model_kwargs",
        ),
    ],
    ids=["yolo", "trocr"],
)
def test_a_model_step_may_not_carry_keys_beside_model_settings(settings, stray):
    """htrflow builds a model's arguments as ``model_settings | settings``,
    so a key beside ``model_settings`` overrides the same key inside it --
    a pinned revision included (audit 2026-09-17, 3058)."""
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {
                "id": "p",
                "image": "ghcr.io/x/y@sha256:" + "a" * 64,
                "steps": [{"step": "Segmentation", "settings": settings}],
            }
        )
    message = " ".join(str(e["msg"]) for e in exc_info.value.errors())
    assert stray in message
    assert "model_settings" in message


def test_steps_that_load_no_model_keep_their_own_settings():
    pipeline = Pipeline.model_validate(
        {
            "id": "p",
            "image": "ghcr.io/x/y@sha256:" + "a" * 64,
            "steps": [
                *[{"step": "Segmentation", "settings": {"model": "yolo"}}] * 2,
                {
                    "step": "TextRecognition",
                    "settings": {
                        "model": "TrOCR",
                        "model_settings": {"model": "o/trocr"},
                        "generation_settings": {"batch_size": 8},
                    },
                },
                {"step": "ReadingOrderMarginalia", "settings": {"two_page": True}},
            ],
        }
    )
    assert len(pipeline.steps) == 4


@pytest.mark.parametrize("name", ["Export", "export"])
def test_a_pipeline_may_not_carry_its_own_export_step(name):
    """The wrapper appends the Export steps; one in the file is refused by the
    wrapper before any model loads (3098), and here before it ever reaches a
    cluster. htrflow looks a step up by its lower-cased name."""
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {
                "id": "p",
                "image": "ghcr.io/x/y@sha256:" + "a" * 64,
                "steps": [
                    {"step": name, "settings": {"dest": "out", "format": "alto"}}
                ],
            }
        )
    message = " ".join(str(e["msg"]) for e in exc_info.value.errors())
    assert "Export" in message
    assert "the wrapper appends" in message
