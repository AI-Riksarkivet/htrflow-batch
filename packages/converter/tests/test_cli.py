import functools
import shutil
from pathlib import Path

import pytest
import yaml

from htrflow_converter import render
from htrflow_converter.cli import main
from htrflow_converter.parse import ValidationError, load

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
REPO_ROOT = Path(__file__).parents[3]
#: A part's volumes under the `small_parts` fixture.
PART = 3


@pytest.fixture
def small_parts(monkeypatch):
    """Parts of ``PART`` volumes instead of 10 000: the same cut, the same
    part names, at a size a test renders in milliseconds rather than
    seconds. The real limits are kept by the tests that are about them
    (the byte budget's re-split, a 63-character name's highest index)."""
    monkeypatch.setattr(render, "split", functools.partial(render.split, size=PART))


EXAMPLES_CAMPAIGNS = REPO_ROOT / "examples" / "campaigns"


def test_validate_examples_campaigns_exits_0(capsys):
    """examples/campaigns is the shape of a real campaigns repo, shown in
    docs and copied by operators — this keeps it from rotting silently as
    the converter's own rules evolve (B63 Task 6)."""
    rc = main(["validate", str(EXAMPLES_CAMPAIGNS)])
    out = capsys.readouterr().out
    assert rc == 0, out


def test_validate_good_repo_exits_0(capsys):
    rc = main(["validate", str(FIXTURES / "good")])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == ""


def test_validate_bad_repo_exits_1_and_prints_problems(capsys):
    rc = main(["validate", str(FIXTURES / "bad" / "bad-image")])
    assert rc == 1
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) >= 1
    assert any("is not pinned to a digest" in line for line in lines)
    # `validate` renders nothing by definition, so it drops that half of the
    # closing line and only counts.
    assert lines[-1] == "1 problem in 1 file"


def test_validate_bad_repo_prints_one_problem_per_line(capsys):
    repo = FIXTURES / "bad" / "multi-file"
    with pytest.raises(ValidationError) as e:
        load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")
    problems = e.value.problems
    assert len(problems) == 2
    assert "has an id with characters that are not allowed" in problems[0]
    assert "is listed twice" in problems[1]
    rc = main(["validate", str(repo)])
    assert rc == 1
    *lines, summary = capsys.readouterr().out.splitlines()
    assert lines == problems, "each problem whole, on a line of its own"
    assert summary == "2 problems in 2 files"


def test_render_says_nothing_was_rendered_and_writes_nothing(tmp_path, capsys):
    """The closing line answers the question the author asks next: did any of
    this reach the out directory? (No -- load() fails before the first write.)"""
    out = tmp_path / "rendered"
    rc = main(["render", str(FIXTURES / "bad" / "multi-file"), "--out", str(out)])
    assert rc == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "2 problems in 2 files — nothing was rendered"
    )
    assert not out.exists()


def test_render_writes_the_expected_file_names(tmp_path):
    out = tmp_path / "rendered"
    rc = main(["render", str(GOOD), "--out", str(out)])
    assert rc == 0
    assert (out / "pipelines" / "demo-v1.yaml").exists()
    assert (out / "campaigns" / "kyrk.yaml").exists()
    assert (out / "campaigns" / "loc.yaml").exists()


def test_render_rejects_an_added_volume_on_an_existing_campaign(tmp_path, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0

    kyrk_path = repo / "campaigns" / "kyrk.yaml"
    doc = yaml.safe_load(kyrk_path.read_text())
    doc["volumes"].append("R9999999")
    kyrk_path.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()

    rc = main(["render", str(repo), "--out", str(out)])
    assert rc == 1
    out_text = capsys.readouterr().out
    assert "campaign kyrk is append-only: create a new campaign" in out_text


def test_validate_refuses_the_change_render_would_refuse(tmp_path, capsys):
    """`validate` is the pull-request gate and `render` runs on main. A rule
    only `render` held was a change that went green in review and then
    stopped every render on main: a swapped volume here (3086)."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    path = repo / "campaigns" / "kyrk.yaml"
    path.write_text(path.read_text().replace("R0001203", "R0001204"))
    capsys.readouterr()

    assert main(["validate", str(repo)]) == 1
    assert "campaign kyrk is append-only" in capsys.readouterr().out


def test_render_a_new_campaign_renders_fine(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0

    (repo / "campaigns" / "brandnew.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R5555555\n"
    )
    rc = main(["render", str(repo), "--out", str(out)])
    assert rc == 0
    assert (out / "campaigns" / "brandnew.yaml").exists()


def test_render_reports_a_clean_error_for_a_corrupt_existing_campaign_file(
    tmp_path, capsys
):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0

    (out / "campaigns" / "kyrk.yaml").write_text("foo: [1, 2\n")
    capsys.readouterr()

    rc = main(["render", str(repo), "--out", str(out)])
    assert rc == 1
    out_text = capsys.readouterr().out
    assert "cannot read existing campaign" in out_text
    assert "Traceback" not in out_text


def test_render_removes_the_manifest_of_a_deleted_campaign(tmp_path):
    """ "Deleting a campaign's file cancels it" is only true if the render it
    is applied from stops producing that Job. A leftover rendered/ file keeps
    the cancelled campaign in every subsequent apply — and survives a prune,
    because the prune compares the cluster against exactly that file."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    rendered = out / "campaigns" / "kyrk.yaml"
    assert rendered.exists()

    (repo / "campaigns" / "kyrk.yaml").unlink()
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert not rendered.exists()
    # Other campaigns and every pipeline are untouched.
    assert list((out / "campaigns").glob("*.yaml"))
    assert (out / "pipelines" / "demo-v1.yaml").exists()


def test_render_removes_a_pipeline_manifest_when_the_pipeline_is_deleted(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    stale = out / "pipelines" / "demo-v1.yaml"
    assert stale.exists()

    # A pipeline cannot be deleted while a campaign still names it, so the
    # campaigns go first.
    for path in (repo / "campaigns").glob("*.yaml"):
        path.unlink()
    (repo / "pipelines" / "demo-v1.yaml").unlink()
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert not stale.exists()


def test_render_refuses_an_out_dir_that_contains_the_sources(tmp_path, capsys):
    """`--out` is a directory render *deletes from* (see `_swap_in`). Pointing it
    at the campaigns repo — or anything above it — would delete the campaigns
    and pipelines it just read."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    for out in (repo, repo / "campaigns", repo / "pipelines", tmp_path):
        assert main(["render", str(repo), "--out", str(out)]) == 1, out
        err = capsys.readouterr().err
        assert "would delete" in err, err
    # nothing was touched
    assert (repo / "campaigns" / "kyrk.yaml").exists()
    assert (repo / "pipelines" / "demo-v1.yaml").exists()


def test_render_prints_every_removed_path_and_also_removes_yml(tmp_path, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    stale_yml = out / "campaigns" / "gone.yml"
    stale_yml.write_text("{}\n")
    (repo / "campaigns" / "kyrk.yaml").unlink()
    capsys.readouterr()

    assert main(["render", str(repo), "--out", str(out)]) == 0
    err = capsys.readouterr().err
    assert f"removed: {out / 'campaigns' / 'kyrk.yaml'}" in err, err
    assert f"removed: {stale_yml}" in err, err
    assert not stale_yml.exists()


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_a_refused_render_leaves_out_exactly_as_it_was(tmp_path, capsys):
    """A render refused over a later campaign used to have written the
    pipelines and the campaigns before it already: a campaign that was never
    applied then counted as rendered, and its volume list was frozen (3089)."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    before = _tree(out)
    doc = yaml.safe_load((repo / "pipelines" / "demo-v1.yaml").read_text())
    doc["image"] = NEW_IMAGE
    (repo / "pipelines" / "demo-v2.yaml").write_text(yaml.safe_dump(doc))
    (repo / "campaigns" / "aaa.yaml").write_text(
        "pipeline: demo-v2\nvolumes:\n  - R5555555\n"
    )
    loc = repo / "campaigns" / "loc.yaml"
    loc.write_text(loc.read_text().replace("R0009998", "R0009997"))
    capsys.readouterr()

    assert main(["render", str(repo), "--out", str(out)]) == 1
    assert "campaign loc is append-only" in capsys.readouterr().out
    assert _tree(out) == before


def test_a_render_that_dies_halfway_leaves_out_as_it_was(tmp_path, capsys, monkeypatch):
    """Nothing lands in --out until every file of the render is written, so
    a crash (a full disk, a bug) cannot leave a mix of two renders in it."""
    from htrflow_converter import render

    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    # Every file this render writes differs from the last one's: the Hub
    # token reaches the warm-up, the window every campaign Job.
    cfg = repo / "converter.yaml"
    cfg.write_text(
        cfg.read_text().replace("window: 10", "window: 4")
        + "hf_token_secret: hf-token\n"
    )
    (repo / "campaigns" / "kyrk.yaml").unlink()
    (repo / "campaigns" / "new.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R5555555\n"
    )
    before = _tree(out)
    real = render.campaign_objects

    def dies_on_the_last(c, p, cfg):
        if c.name == "new":
            raise OSError("No space left on device")
        return real(c, p, cfg)

    monkeypatch.setattr(render, "campaign_objects", dies_on_the_last)
    with pytest.raises(OSError):
        main(["render", str(repo), "--out", str(out)])
    assert _tree(out) == before
    assert sorted(p.name for p in repo.iterdir()) == sorted(
        ["campaigns", "converter.yaml", "pipelines", "rendered"]
    ), "the temp directory beside --out was left behind"


def test_render_leaves_what_else_is_in_out_alone(tmp_path):
    """--out is the converter's for pipelines/, campaigns/ and sync.yaml;
    anything else in it (a README beside rendered/'s files) is not."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    out.mkdir()
    (out / "README.md").write_text("kept\n")
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert (out / "README.md").read_text() == "kept\n"


def test_append_only_still_finds_the_parts_of_a_cut_down_campaign_name(
    tmp_path, capsys, small_parts
):
    """A campaign that splits renders under a shortened name, so `rendered/`
    holds `<shortened>-partN.yaml`. The append-only check has to look for
    those: hunting for files under the campaign's own full name would find
    none and wave a changed volume list through."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    name = "a" * 58
    path = repo / "campaigns" / f"{name}.yaml"
    path.write_text(_split_campaign(PART + 1))
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0

    parts = sorted((out / "campaigns").glob("a*-part*.yaml"))
    assert len(parts) == 2
    assert not (out / "campaigns" / f"{name}-part1.yaml").exists()
    for i, part in enumerate(parts, start=1):
        assert len(part.stem) <= 63
        assert name.startswith(part.stem.removesuffix(f"-part{i}"))

    path.write_text(_split_campaign(PART + 2))
    capsys.readouterr()
    assert main(["render", str(repo), "--out", str(out)]) == 1
    assert f"campaign {name} is append-only" in capsys.readouterr().out


def test_a_campaign_named_like_a_part_of_another_is_not_taken_for_one(tmp_path, capsys):
    """`loc-partner` begins with `loc-part`, and a glob for loc's parts
    (`loc-part*.yaml`) found it: every render after the first held loc
    against loc's volumes and loc-partner's together, and refused the repo
    as append-only. A part is `-part` and a number, and nothing after."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    (repo / "campaigns" / "loc-partner.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R5555555\n"
    )
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert main(["render", str(repo), "--out", str(out)]) == 0, capsys.readouterr()
    assert main(["validate", str(repo)]) == 0, capsys.readouterr()


def _images_volumes(count: int, pages: int = 300) -> list[dict]:
    url = (
        "https://images.example.org/archives!R00012345/jp2/00000000000000000{:03d}.jpg"
    )
    return [
        {"id": f"vol{v:04d}", "images": [url.format(p) for p in range(pages)]}
        for v in range(count)
    ]


def test_render_refuses_to_re_split_a_campaign_that_is_already_rendered(
    tmp_path, capsys
):
    """A campaign between the byte budget and the 1 MiB limit rendered as one
    file before this rule existed and was applied that way. Re-rendering it
    unchanged must not quietly rename it into parts: `apply --prune` would
    delete the Job that has already run every volume and start over."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    volumes = _images_volumes(41)
    text = "\n".join(f"{v['id']}\timages:{' '.join(v['images'])}" for v in volumes)
    assert 900 * 1024 < len(text.encode()) < 1024 * 1024  # one file before, two now
    (repo / "campaigns" / "wide.yaml").write_text(
        yaml.safe_dump({"pipeline": "demo-v1", "volumes": volumes})
    )
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    # what the previous rule left behind: one file, one Job, one ConfigMap
    single = out / "campaigns" / "wide.yaml"
    for path in (out / "campaigns").glob("wide-part*.yaml"):
        path.unlink()
    single.write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": "campaign-wide", "namespace": "htr-test"},
                    "data": {"volumes.txt": text + "\n"},
                },
                {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {"name": "wide", "namespace": "htr-test"},
                },
            ],
            sort_keys=False,
        )
    )
    capsys.readouterr()

    assert main(["render", str(repo), "--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "campaign wide was rendered as wide.yaml" in printed, printed
    assert "create a new campaign" in printed
    assert single.exists()  # nothing removed, nothing renamed
    assert not list((out / "campaigns").glob("wide-part*.yaml"))
    assert main(["validate", str(repo)]) == 1  # and a pull request says so


def _split_campaign(volumes: int) -> str:
    return "pipeline: demo-v1\nvolumes:\n" + "".join(
        f"  - id: v{i}\n    manifest: https://example.org/{i}\n" for i in range(volumes)
    )


@pytest.mark.parametrize("volumes", [(PART + 1, PART + 1), (PART + 1, PART + 2)])
def test_render_refuses_two_campaigns_whose_split_names_collide(
    tmp_path, capsys, small_parts, volumes
):
    """Cutting a long name to a stem can make two campaigns share it. Both
    would render into the same files, the second one silently overwriting the
    first. With DIFFERENT volume lists the second one used to find the
    first's parts in a fresh rendered/ and be reported as append-only --
    exactly the misleading sentence this check exists to replace, so the
    collision is looked for across every campaign before any of them is
    compared with an earlier render."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    shared = "k" * 50
    for tail, count in zip(("alpha", "beta"), volumes):
        (repo / "campaigns" / f"{shared}-{tail}.yaml").write_text(
            _split_campaign(count)
        )
    out = repo / "rendered"

    assert main(["render", str(repo), "--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert f"campaigns/{shared}-alpha.yaml" in printed, printed
    assert f"campaigns/{shared}-beta.yaml" in printed
    assert f"{shared}-part1.yaml" in printed
    assert "rename one" in printed
    assert "append-only" not in printed


def test_validate_refuses_colliding_split_names_too(tmp_path, capsys, small_parts):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    for tail in ("alpha", "beta"):
        (repo / "campaigns" / f"{'k' * 50}-{tail}.yaml").write_text(
            _split_campaign(PART + 1)
        )
    assert main(["validate", str(repo)]) == 1
    assert "rename one" in capsys.readouterr().out


def _rewrite_volumes_txt(path: Path, old: str, new: str) -> str:
    """Edit a rendered campaign file's volumes.txt through the YAML, not its
    bytes: the value is one long double-quoted scalar and where the emitter
    folds it is its own business."""
    docs = list(yaml.safe_load_all(path.read_text()))
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    text = cm["data"]["volumes.txt"]
    assert old in text
    cm["data"]["volumes.txt"] = text.replace(old, new)
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    return cm["data"]["volumes.txt"]


def _volumes_txt_of(path: Path) -> str:
    docs = list(yaml.safe_load_all(path.read_text()))
    return next(d for d in docs if d["kind"] == "ConfigMap")["data"]["volumes.txt"]


SCANS_SPACED = "images:https://example.org/scan1.jpg https://example.org/scan2.jpg"
SCANS_COMMAED = "images:https://example.org/scan1.jpg,https://example.org/scan2.jpg"


def test_a_comma_rendered_campaign_is_unchanged_and_is_rewritten_with_spaces(
    tmp_path, capsys
):
    """Every campaign in every campaigns repo was rendered with commas
    between an `images:` volume's URLs. The append-only check compares what
    the line MEANS, not its bytes, or the separator change would report each
    of them as append-only and there would be no way to re-render any of
    them."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    rendered = out / "campaigns" / "kyrk.yaml"
    _rewrite_volumes_txt(rendered, SCANS_SPACED, SCANS_COMMAED)
    capsys.readouterr()

    assert main(["render", str(repo), "--out", str(out)]) == 0, capsys.readouterr().out
    assert "append-only" not in capsys.readouterr().out
    assert SCANS_SPACED in _volumes_txt_of(rendered)


def test_a_comma_rendered_campaign_whose_volumes_changed_is_still_refused(
    tmp_path, capsys
):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    _rewrite_volumes_txt(
        out / "campaigns" / "kyrk.yaml",
        SCANS_SPACED,
        "images:https://example.org/scan1.jpg,https://example.org/scan3.jpg",
    )
    capsys.readouterr()
    assert main(["render", str(repo), "--out", str(out)]) == 1
    assert "campaign kyrk is append-only" in capsys.readouterr().out


def _edit_pipeline(repo: Path, **changes) -> None:
    path = repo / "pipelines" / "demo-v1.yaml"
    doc = yaml.safe_load(path.read_text())
    doc.update(changes)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


NEW_IMAGE = "ghcr.io/riksarkivet/htrflow-batch@sha256:" + "b" * 64


def test_render_refuses_a_pipeline_edit_a_rendered_campaign_still_runs(
    tmp_path, capsys
):
    """A pipeline id is a permanent name for a recipe. Editing one that a
    campaign already runs used to reach the API server as "field is
    immutable", halfway through an apply; it is now one sentence here, and
    nothing was rendered."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    _edit_pipeline(repo, image=NEW_IMAGE)
    capsys.readouterr()

    assert main(["render", str(repo), "--out", str(out)]) == 1
    assert capsys.readouterr().out.strip() == (
        "pipeline demo-v1 changed (image) but campaigns kyrk, loc still run "
        "it — a pipeline is immutable while campaigns reference it; add a new "
        "pipeline file (demo-v1-2) and point new campaigns at it"
    )
    assert NEW_IMAGE not in (out / "pipelines" / "demo-v1.yaml").read_text()


def test_validate_refuses_the_same_edit_before_anything_is_rendered(tmp_path, capsys):
    """`validate` is what a pull request runs, and `rendered/` is committed,
    so the previous render is right there to be held against."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    _edit_pipeline(repo, steps=[{"step": "Segmentation"}])
    capsys.readouterr()

    assert main(["validate", str(repo)]) == 1
    assert "pipeline demo-v1 changed (steps)" in capsys.readouterr().out


def test_a_new_pipeline_id_goes_through(tmp_path, capsys):
    """The way out the sentence names: a new file, and new campaigns on it."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0

    doc = yaml.safe_load((repo / "pipelines" / "demo-v1.yaml").read_text())
    doc["image"] = NEW_IMAGE
    (repo / "pipelines" / "demo-v1-2.yaml").write_text(yaml.safe_dump(doc))
    (repo / "campaigns" / "brandnew.yaml").write_text(
        "pipeline: demo-v1-2\nvolumes:\n  - R5555555\n"
    )
    assert main(["render", str(repo), "--out", str(out)]) == 0, capsys.readouterr().out
    assert (out / "pipelines" / "demo-v1-2.yaml").exists()


def test_a_pipeline_no_rendered_campaign_runs_may_still_be_edited(tmp_path, capsys):
    """Nothing is keyed by an id no campaign has run under. A campaign whose
    file has been removed -- how a finished campaign is retired -- releases
    the pipeline the same way."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    for path in (repo / "campaigns").glob("*.yaml"):
        path.unlink()
    assert main(["render", str(repo), "--out", str(out)]) == 0
    _edit_pipeline(repo, image=NEW_IMAGE)
    (repo / "campaigns" / "later.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R5555555\n"
    )
    assert main(["render", str(repo), "--out", str(out)]) == 0, capsys.readouterr().out
    assert NEW_IMAGE in (out / "pipelines" / "demo-v1.yaml").read_text()


def test_a_converter_yaml_setting_is_not_a_changed_recipe(tmp_path, capsys):
    """The other half of the rule. A converter.yaml setting reaches every
    warm-up Job's pod template at once, and so does a converter release --
    neither is a changed recipe, and `apply` replaces the warm-up for those
    rather than refusing the render."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    cfg = repo / "converter.yaml"
    cfg.write_text(cfg.read_text() + "hf_token_secret: hf-token\n")
    assert main(["render", str(repo), "--out", str(out)]) == 0, capsys.readouterr().out
    assert "HF_TOKEN" in (out / "pipelines" / "demo-v1.yaml").read_text()


def test_reordering_the_keys_of_a_step_is_not_a_changed_recipe(tmp_path, capsys):
    """The recipe is compared parsed, never as bytes: `settings:` written
    above `step:` is the same pipeline, and a PyYAML that spells a mapping
    differently one release from now must not flag every untouched file."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    path = repo / "pipelines" / "demo-v1.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["steps"] = [dict(reversed(list(s.items()))) for s in doc["steps"]]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()

    assert main(["render", str(repo), "--out", str(out)]) == 0, capsys.readouterr().out


def test_a_changed_model_id_is_a_changed_recipe(tmp_path, capsys):
    """The other half: a step that says something else IS a new recipe."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    path = repo / "pipelines" / "demo-v1.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["steps"][0]["settings"]["model_settings"]["model"] = "Riksarkivet/other-1"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()

    assert main(["render", str(repo), "--out", str(out)]) == 1
    assert "pipeline demo-v1 changed (steps)" in capsys.readouterr().out


def test_validate_refuses_a_repo_without_a_converter_yaml(tmp_path, capsys):
    """`converter.yaml` is what says which namespace, queue, Secret and PVC a
    campaign belongs to. A repo without one is not a campaigns repo, and
    every one of those settings quietly falling back to a default is how an
    apply reaches the wrong namespace."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    (repo / "converter.yaml").unlink()
    assert main(["validate", str(repo)]) == 1
    printed = capsys.readouterr().out
    assert str(repo / "converter.yaml") in printed
    assert "htrflow-campaigns init" in printed
    assert main(["render", str(repo), "--out", str(tmp_path / "rendered")]) == 1
    assert not (tmp_path / "rendered").exists()


# --- only `htrflow-campaigns apply` applies rendered/ (3085) --------------

ARGO_HOOK = "argocd.argoproj.io/hook"


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def test_argo_cd_never_applies_a_rendered_object(tmp_path):
    """An Argo CD Application syncing `rendered/` used to own every Job in
    it: once the TTL reaped a finished campaign's Job, sync (or self-heal)
    created it again and every volume ran again -- past the finished-campaign
    guard, the live-record check and the pause sync, which all live in
    `apply`. Every rendered object is a Skip hook to Argo CD, so it applies,
    re-creates, heals and prunes none of them."""
    out = tmp_path / "rendered"
    assert main(["render", str(GOOD), "--out", str(out)]) == 0
    objects = [d for p in sorted(out.glob("*/*.yaml")) for d in _docs(p)]
    assert {o["kind"] for o in objects} == {"ConfigMap", "Job"}
    for o in objects:
        assert o["metadata"]["annotations"][ARGO_HOOK] == "Skip", o["metadata"]


def test_the_render_carries_one_object_argo_cd_syncs(tmp_path):
    """With every rendered object skipped, an Application would never go
    OutOfSync, and automated sync -- which runs only on OutOfSync -- would
    never run the hook that applies. `rendered/sync.yaml` is the one object
    it syncs: a digest of the render, so each new render is a sync. It is
    unlabelled, so `apply --prune` never deletes it, and it is the same
    for the same repo (rendered/ is a pure function of the repo)."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0
    (sync,) = _docs(out / "sync.yaml")
    assert sync["kind"] == "ConfigMap"
    assert sync["metadata"]["namespace"] == "htr-test"
    assert "labels" not in sync["metadata"]
    assert ARGO_HOOK not in (sync["metadata"].get("annotations") or {})
    first = sync["data"]["sha256"]
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert _docs(out / "sync.yaml")[0]["data"]["sha256"] == first
    kyrk = repo / "campaigns" / "kyrk.yaml"
    kyrk.write_text(kyrk.read_text() + "suspend: true\n")
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert _docs(out / "sync.yaml")[0]["data"]["sha256"] != first


def _validate_with(tmp_path, capsys, files: dict[str, str]) -> tuple[int, str]:
    """``validate`` over the good repo plus ``files`` (path -> text)."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    for rel, text in files.items():
        (repo / rel).write_text(text)
    capsys.readouterr()
    rc = main(["validate", str(repo)])
    return rc, capsys.readouterr().out


def _campaign(volumes: int, pipeline: str = "demo-v1") -> str:
    return f"pipeline: {pipeline}\nvolumes:\n" + "".join(
        f"  - R{i:07d}\n" for i in range(volumes)
    )


# The API server's rules for a campaign's Indexed Job (3087): its name is a
# DNS-1123 subdomain, AND `<name>-<completions - 1>` -- the hostname of its
# last pod -- is a DNS-1123 label: no dots, at most 63 characters
# ("will not able to create pod with invalid DNS label").
@pytest.mark.parametrize(
    ("name", "volumes", "ok"),
    [
        ("kyrk.1850", 1, False),  # a dot: every pod's hostname is refused
        ("a" * 61, 10, True),  # a...a-9 is 63
        ("a" * 61, 11, False),  # a...a-10 is 64
        ("a" * 62, 1, False),  # a...a-0 is 64: refused with a single volume
        ("htr-warmup-demo-v1", 1, False),  # the warm-up Job's own name
    ],
)
def test_a_campaign_name_the_api_server_would_refuse_is_refused_here(
    tmp_path, capsys, name, volumes, ok
):
    files = {f"campaigns/{name}.yaml": _campaign(volumes)}
    rc, out = _validate_with(tmp_path, capsys, files)
    assert (rc == 0) == ok, out


def test_the_index_suffix_rule_names_the_last_pod(tmp_path, capsys):
    name = "a" * 61
    rc, out = _validate_with(
        tmp_path, capsys, {f"campaigns/{name}.yaml": _campaign(11)}
    )
    assert rc == 1
    assert f"{name}-10" in out and "63" in out, out


def test_a_long_campaign_that_splits_is_still_fine(tmp_path, capsys):
    """Its parts are named from a stem cut short enough for any index."""
    rc, out = _validate_with(
        tmp_path, capsys, {f"campaigns/{'a' * 63}.yaml": _split_campaign(10_001)}
    )
    assert rc == 0, out


# The warm-up Job is `htr-warmup-<id>`, and the Job controller copies a Job's
# name into its pods' `job-name` label, which stops at 63 characters.
@pytest.mark.parametrize(("length", "ok"), [(52, True), (53, False)])
def test_a_pipeline_id_leaves_room_for_its_warm_up_job(tmp_path, capsys, length, ok):
    pid = "p" * length
    pipeline = (GOOD / "pipelines" / "demo-v1.yaml").read_text()
    files = {f"pipelines/{pid}.yaml": pipeline, "campaigns/x.yaml": _campaign(1, pid)}
    rc, out = _validate_with(tmp_path, capsys, files)
    assert (rc == 0) == ok, out


def test_a_split_campaign_beside_a_single_one_sharing_its_stem_is_not_append_only(
    tmp_path, capsys, small_parts
):
    """audit 0923 C-4: parts were found by the first 50 characters of a name.
    With `<stem>-b` rendered as one Job, a big `<stem>-a` added beside it
    renders `<stem>-part1` and `-part2`, and the next validate held those
    against `<stem>-b`: "append-only", with nothing changed, and every later
    render refused. Parts belong to the campaign their label names."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    stem = "k" * 50
    (repo / "campaigns" / f"{stem}-b.yaml").write_text(_split_campaign(PART))
    out = repo / "rendered"
    assert main(["render", str(repo), "--out", str(out)]) == 0

    (repo / "campaigns" / f"{stem}-a.yaml").write_text(_split_campaign(PART + 1))
    assert main(["validate", str(repo)]) == 0, capsys.readouterr().out
    assert main(["render", str(repo), "--out", str(out)]) == 0
    assert (out / "campaigns" / f"{stem}-part2.yaml").is_file()

    capsys.readouterr()
    assert main(["validate", str(repo)]) == 0, capsys.readouterr().out
    assert main(["render", str(repo), "--out", str(out)]) == 0, capsys.readouterr()

    # and the rule still holds for each of them, on its own files
    (repo / "campaigns" / f"{stem}-b.yaml").write_text(_split_campaign(PART - 1))
    assert main(["validate", str(repo)]) == 1
    assert f"campaign {stem}-b is append-only" in capsys.readouterr().out


def _window_repo(tmp_path, campaign: str):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    for f in (repo / "campaigns").glob("*.yaml"):
        f.unlink()
    (repo / "campaigns" / "big.yaml").write_text(
        "pipeline: demo-v1\nvolumes: [R1, R2, R3, R4, R5, R6]\n" + campaign
    )
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    return repo


@pytest.mark.parametrize(
    "edit,before,after",
    [
        (("campaigns/big.yaml", "window: 3\n", "window: 4\n"), 3, 4),
        (("converter.yaml", "window: 10\n", "window: 2\n"), 3, 2),
    ],
)
def test_a_window_change_under_a_rendered_campaign_is_warned_about(
    tmp_path, capsys, edit, before, after
):
    """audit 0923 C-11: Kueue (v0.19, the version the Makefile installs)
    compares a running Job's pod count, min(parallelism, completions), with
    its admitted Workload's; when they differ it suspends the Job -- every
    running pod stopped -- deletes the Workload and queues the campaign
    again (jobframework `EquivalentToWorkload` / `ensureOneWorkload`: "No
    matching Workload"). Whether the campaign is running is the cluster's to
    say, not the repo's -- a finished or never-admitted one changes nothing
    -- so offline this is a warning with the safe way, and the apply, which
    sees the cluster, holds a running one (review 6)."""
    repo = _window_repo(tmp_path, "window: 3\n")
    rel, old, new = edit
    path = repo / rel
    path.write_text(path.read_text().replace(old, new))
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 0
    said = capsys.readouterr()
    assert said.out == ""
    assert said.err.startswith(
        f"warning: campaign big runs {before} pods at a time and would now run {after}"
    ), said.err
    assert "pause it first" in said.err
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    assert "warning: campaign big runs" in capsys.readouterr().err


def test_a_window_change_that_moves_no_pod_count_is_allowed(tmp_path, capsys):
    """Six volumes at a window of 10 or 8 is six pods either way: the count
    Kueue compares does not move, so neither does anything running."""
    repo = _window_repo(tmp_path, "window: 10\n")
    path = repo / "campaigns" / "big.yaml"
    path.write_text(path.read_text().replace("window: 10\n", "window: 8\n"))
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 0
    assert capsys.readouterr().err == ""


def test_a_window_change_on_a_campaign_paused_before_and_after_is_allowed(
    tmp_path, capsys
):
    """A paused campaign runs no pods, and its Workload holds no quota, which
    Kueue updates in place to the new count: pause, change the window, then
    resume is the way to change it without a restart."""
    repo = _window_repo(tmp_path, "window: 3\nsuspend: true\n")
    path = repo / "campaigns" / "big.yaml"
    path.write_text(path.read_text().replace("window: 3\n", "window: 4\n"))
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 0
    assert capsys.readouterr().err == ""  # nothing to warn about


def _without_git(monkeypatch) -> None:
    """No git binary: what the hook's distroless image has."""
    import subprocess

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)


def test_the_commit_is_read_without_a_git_binary(tmp_path, monkeypatch, commit_all):
    """audit 0923 C-10: the Argo CD hook's image is distroless, with no git,
    so every campaign it applied recorded commit "unknown". The clone in the
    same image is dulwich's; so is the read of what it cloned -- from the
    campaigns directory, below the checkout's root."""
    from htrflow_converter import cli

    (tmp_path / "campaigns").mkdir()
    (tmp_path / "campaigns" / "a.yaml").write_text("pipeline: p\n")
    sha = commit_all(tmp_path)
    _without_git(monkeypatch)
    assert cli._git_head(tmp_path / "campaigns") == sha


def test_outside_a_checkout_the_commit_is_still_unknown(tmp_path, monkeypatch):
    from htrflow_converter import cli

    _without_git(monkeypatch)
    assert cli._git_head(tmp_path) == "unknown"


def test_a_checkout_with_no_commit_yet_is_unknown(tmp_path, monkeypatch):
    from dulwich import porcelain

    from htrflow_converter import cli

    porcelain.init(str(tmp_path))
    _without_git(monkeypatch)
    assert cli._git_head(tmp_path) == "unknown"


def test_without_git_or_dulwich_the_commit_is_unknown(tmp_path, monkeypatch):
    """The one case a real dulwich cannot show: an install without the
    `hook` extra, on a machine without git."""
    import sys

    from htrflow_converter import cli

    _without_git(monkeypatch)
    monkeypatch.setitem(sys.modules, "dulwich.repo", None)  # import fails
    assert cli._git_head(tmp_path) == "unknown"


def test_validate_rendered_passes_a_checkout_whose_render_is_committed(
    tmp_path, capsys
):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    assert main(["validate", "--rendered", str(repo)]) == 0, capsys.readouterr()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("committed", [True, False])
def test_validate_rendered_refuses_a_checkout_ci_has_not_rendered(
    tmp_path, capsys, committed
):
    """audit 0923 S-9: the Argo CD hook clones the branch's HEAD, not the
    commit CI rendered, so a push landing after CI's render commit was
    applied unrendered and unchecked by the Policy job. The hook runs this
    before the apply: HEAD's own render has to be the rendered/ HEAD
    carries."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    if committed:
        assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    (repo / "campaigns" / "new.yaml").write_text("pipeline: demo-v1\nvolumes: [N1]\n")
    capsys.readouterr()
    assert main(["validate", "--rendered", str(repo)]) == 1
    said = capsys.readouterr().out
    assert said.startswith(f"{repo / 'rendered'} is not what this checkout renders")
    assert "nothing CI did not render" in said
    assert main(["validate", str(repo)]) == 0  # a pull request is not held to it


def _recorded_repo(tmp_path, recorded: str, now: str):
    """A repo whose `rendered/` holds `recorded`'s render -- what v0.5.0 made
    of `now`, which the current rules refuse -- and whose campaign file now
    says `now`."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    campaign = repo / "campaigns" / "kyrk.yaml"
    campaign.write_text(f"pipeline: demo-v1\nvolumes:\n{recorded}")
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    campaign.write_text(f"pipeline: demo-v1\nvolumes:\n{now}")
    return repo


def test_a_rendered_campaign_keeps_the_id_yaml_gave_it(tmp_path, capsys):
    """audit 0923 review 3: v0.5.0 rendered `id: 0012345` as volume `5349`.
    Its campaign is append-only, so the quoted `"0012345"` the new rule
    asks for would be refused as a change. Unchanged, it stays valid, with a
    warning naming the id it actually has."""
    m = "    manifest: https://iiif.example.org/m\n"
    repo = _recorded_repo(tmp_path, f'  - id: "5349"\n{m}', f"  - id: 0012345\n{m}")
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 0
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.startswith("warning: campaigns/kyrk.yaml: volume 1"), printed
    assert 'id: "5349"' in printed.err
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0


def test_a_rendered_campaign_that_changes_meets_the_new_rules(tmp_path, capsys):
    m = "    manifest: https://iiif.example.org/m\n"
    repo = _recorded_repo(
        tmp_path, f'  - id: "5349"\n{m}', f"  - id: 0012345\n{m}  - id: R2\n{m}"
    )
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 1
    assert "has an id that YAML reads as a number" in capsys.readouterr().out


def test_a_rendered_campaign_keeps_a_url_the_new_rule_refuses(tmp_path, capsys):
    """audit 0923 review 4: the same for a source URL v0.5.0 took."""
    url = "https://example.org:99999/m"
    repo = _recorded_repo(
        tmp_path, "  - id: R1\n    manifest: https://x.example/m\n", ""
    )
    # the record, as v0.5.0 wrote it with this URL
    path = repo / "rendered" / "campaigns" / "kyrk.yaml"
    path.write_text(path.read_text().replace("https://x.example/m", url))
    (repo / "campaigns" / "kyrk.yaml").write_text(
        f"pipeline: demo-v1\nvolumes:\n  - id: R1\n    manifest: {url}\n"
    )
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 0
    err = capsys.readouterr().err
    assert "warning: campaigns/kyrk.yaml: volume 1 has a manifest a browser" in err
    assert "its port is not a number" in err

    (repo / "campaigns" / "new.yaml").write_text(
        f"pipeline: demo-v1\nvolumes:\n  - id: N1\n    manifest: {url}\n"
    )
    assert main(["validate", str(repo)]) == 1  # a new campaign is held to it


def test_a_rendered_campaign_keeps_bare_codes_a_template_no_longer_expands(
    tmp_path, capsys
):
    """audit 0923 ruling 1: source_template lost its built-in host. A repo
    rendered under it, whose converter.yaml never set one, is not locked: an
    unchanged campaign keeps the URLs its record holds, with a warning; a new
    or changed one must set the template."""
    repo = _recorded_repo(tmp_path, "  - R1\n  - R2\n", "  - R1\n  - R2\n")
    before = (repo / "rendered" / "campaigns" / "kyrk.yaml").read_text()
    config = repo / "converter.yaml"
    config.write_text(
        "".join(
            line
            for line in config.read_text().splitlines(keepends=True)
            if not line.startswith("source_template:")
        )
    )
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 0
    printed = capsys.readouterr()
    assert printed.out == ""
    (warning,) = [w for w in printed.err.splitlines() if "kyrk.yaml" in w]
    assert warning.startswith("warning: campaigns/kyrk.yaml: its bare reference")
    assert "source_template" in warning
    assert main(["validate", "--rendered", str(repo)]) == 0
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    after = (repo / "rendered" / "campaigns" / "kyrk.yaml").read_text()
    assert after == before

    (repo / "campaigns" / "kyrk.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R1\n  - R2\n  - R3\n"
    )
    capsys.readouterr()
    assert main(["validate", str(repo)]) == 1  # changed: held to the rule
    out = capsys.readouterr().out
    assert "campaigns/kyrk.yaml: volumes" in out
    assert "no source_template" in out


def test_validate_rendered_compares_what_the_render_says_not_its_bytes(
    tmp_path, capsys
):
    """audit 0923 review 5: a checkout with CRLF line endings (git's autocrlf)
    failed the hook's check with nothing changed, and so would a PyYAML
    release that spells the same objects differently. The check compares the
    rendered objects."""
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    assert main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    for path in (repo / "rendered").rglob("*.yaml"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    path = repo / "rendered" / "campaigns" / "loc.yaml"
    path.write_text(yaml.safe_dump_all(yaml.safe_load_all(path.read_text()), width=40))
    assert main(["validate", "--rendered", str(repo)]) == 0, capsys.readouterr()

    docs = list(yaml.safe_load_all(path.read_text()))
    docs[1]["spec"]["parallelism"] = 1
    path.write_text(yaml.safe_dump_all(docs))
    assert main(["validate", "--rendered", str(repo)]) == 1
