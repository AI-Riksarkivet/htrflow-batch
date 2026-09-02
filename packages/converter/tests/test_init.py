"""``htrflow-campaigns init <dir>`` writes the packaged campaigns-repo
template so a new repo (I15) is one command away and never drifts from the
docs' example (see ``test_packaging.py::test_examples_match_template`` for
the drift guard)."""

from pathlib import Path

from htrflow_converter.cli import main


def test_init_writes_a_repo_that_validates(tmp_path, capsys):
    dest = tmp_path / "my-campaigns"
    rc = main(["init", str(dest)])
    assert rc == 0
    capsys.readouterr()

    assert (dest / "converter.yaml").exists()
    assert (dest / "campaigns" / "demo.yaml").exists()
    assert (dest / "pipelines" / "demo-v1.yaml").exists()
    assert (dest / ".github" / "workflows" / "render.yml").exists()
    assert (dest / "README.md").exists()

    assert main(["validate", str(dest)]) == 0


def test_init_prints_next_steps(tmp_path, capsys):
    dest = tmp_path / "my-campaigns"
    rc = main(["init", str(dest)])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(dest) in out
    assert "validate" in out
    # Task 20 G's human-voice rule: no internal names in a message a person
    # reads -- "importlib.resources", "ConfigMap", "template/" never appear.
    for internal in ("importlib", "ConfigMap", "template/", "Traceback"):
        assert internal not in out


def test_init_refuses_a_nonempty_dir_without_force(tmp_path, capsys):
    dest = tmp_path / "my-campaigns"
    dest.mkdir()
    (dest / "keep-me.txt").write_text("do not touch\n")

    rc = main(["init", str(dest)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not empty" in err
    assert "--force" in err
    # Nothing was written or removed.
    assert list(dest.iterdir()) == [dest / "keep-me.txt"]
    assert (dest / "keep-me.txt").read_text() == "do not touch\n"


def test_init_force_overwrites_a_nonempty_dir(tmp_path, capsys):
    dest = tmp_path / "my-campaigns"
    dest.mkdir()
    (dest / "stale.txt").write_text("old\n")

    rc = main(["init", str(dest), "--force"])
    capsys.readouterr()
    assert rc == 0
    assert (dest / "converter.yaml").exists()
    assert main(["validate", str(dest)]) == 0


def test_init_creates_missing_parent_directories(tmp_path, capsys):
    dest = tmp_path / "nested" / "does" / "not" / "exist" / "yet"
    rc = main(["init", str(dest)])
    capsys.readouterr()
    assert rc == 0
    assert (dest / "converter.yaml").exists()


def test_init_into_an_existing_empty_dir_needs_no_force(tmp_path, capsys):
    dest = tmp_path / "my-campaigns"
    dest.mkdir()
    rc = main(["init", str(dest)])
    capsys.readouterr()
    assert rc == 0
    assert (dest / "converter.yaml").exists()
