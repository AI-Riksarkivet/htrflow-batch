import shutil
from pathlib import Path

import yaml

from htrflow_converter.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"


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
