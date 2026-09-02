import shutil
from pathlib import Path

import yaml

from htrflow_converter.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
REPO_ROOT = Path(__file__).parents[3]
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
    assert any("image must be digest-pinned" in line for line in lines)


def test_validate_bad_repo_prints_one_problem_per_line(capsys):
    rc = main(["validate", str(FIXTURES / "bad" / "multi-file")])
    assert rc == 1
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert any("unsafe volume id" in line for line in lines)
    assert any("duplicate volume id" in line for line in lines)


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
    out = tmp_path / "rendered"
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


def test_render_a_new_campaign_renders_fine(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    out = tmp_path / "rendered"
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
    out = tmp_path / "rendered"
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
    out = tmp_path / "rendered"
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
    out = tmp_path / "rendered"
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
    """`--out` is a directory render *deletes from* (see `_prune`). Pointing it
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
    out = tmp_path / "rendered"
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


def test_the_makefile_no_longer_defines_the_prune_selector():
    """One definition, in `render.CAMPAIGN_SELECTOR`: `htrflow-campaigns
    apply` passes it to `kubectl --prune` and `make campaigns-apply` calls
    that. A second copy in the Makefile could drift from the label the
    renderer writes, and a prune that matches nothing deletes nothing --
    silently."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "CAMPAIGN_SELECTOR :=" not in makefile
    assert "htrflow-campaigns apply $(DIR)" in makefile


def test_the_pause_sync_script_is_gone():
    """`htrflow-campaigns apply` owns the Workload sync now; a stale copy of
    the shell script would be a second, silently diverging implementation."""
    assert not (REPO_ROOT / "scripts" / "kueue-pause-sync.sh").exists()
