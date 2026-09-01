from pathlib import Path

from htrflow_converter.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


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
