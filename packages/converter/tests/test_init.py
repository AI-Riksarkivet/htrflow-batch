"""``htrflow-campaigns init <dir>`` writes the packaged campaigns-repo
template so a new repo (I15) is one command away and never drifts from the
docs' example (see ``test_packaging.py::test_examples_match_template`` for
the drift guard)."""

import re
from importlib import resources

import yaml

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


def test_init_defaults_to_github_ci(tmp_path, capsys):
    dest = tmp_path / "c"
    assert main(["init", str(dest)]) == 0
    assert (dest / ".github" / "workflows" / "render.yml").exists()
    assert not (dest / "azure-pipelines.yml").exists()
    assert (dest / "argocd" / "apply.yaml").exists()


def test_init_azure_writes_azure_pipelines_and_no_github(tmp_path, capsys):
    dest = tmp_path / "c"
    assert main(["init", str(dest), "--ci", "azure"]) == 0
    assert (dest / "azure-pipelines.yml").exists()
    assert not (dest / ".github").exists()
    assert (dest / "argocd" / "apply.yaml").exists()
    assert main(["validate", str(dest)]) == 0


def test_the_azure_pipeline_validates_on_prs_and_renders_on_main(tmp_path, capsys):
    dest = tmp_path / "c"
    main(["init", str(dest), "--ci", "azure"])
    text = (dest / "azure-pipelines.yml").read_text()
    for step in (
        "htrflow-campaigns validate .",
        "kyverno apply",
        "htrflow-campaigns render . --out rendered",
    ):
        assert step in text
    doc = yaml.safe_load(text)
    assert doc["trigger"]["branches"]["include"] == ["main"]


def _azure_steps(doc, stage=None):
    return [
        step
        for st in doc["stages"]
        if stage is None or st["stage"] == stage
        for job in st["jobs"]
        for step in job["steps"]
    ]


def test_only_the_azure_push_step_holds_the_push_token(tmp_path, capsys):
    """The build service token reaches the one step that pushes, on main
    only. A persisted checkout credential would sit in .git/config while
    the converter and its dependencies install from the network."""
    dest = tmp_path / "c"
    main(["init", str(dest), "--ci", "azure"])
    text = (dest / "azure-pipelines.yml").read_text()
    doc = yaml.safe_load(text)

    render = next(s for s in doc["stages"] if s["stage"] == "Render")
    assert render["condition"] == (
        "and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))"
    )
    for step in _azure_steps(doc):
        if "checkout" in step:
            assert "persistCredentials" not in step, step

    pushes = [
        s for s in _azure_steps(doc, "Render") if " push origin " in s.get("bash", "")
    ]
    assert len(pushes) == 1
    push = pushes[0]
    assert push["env"] == {"SYSTEM_ACCESSTOKEN": "$(System.AccessToken)"}
    assert text.count("System.AccessToken") == 1
    others = [s for s in _azure_steps(doc) if s is not push]
    assert not any("SYSTEM_ACCESSTOKEN" in str(s) for s in others)
    assert "[skip ci]" in push["bash"]
    assert "HEAD:main" in push["bash"]


def test_the_azure_stages_share_one_uv_install_template(tmp_path, capsys):
    """The checksum-verified uv install is written once, as a steps template
    `init --ci azure` lays down beside the pipeline, and every stage that
    runs the converter includes it -- three copies drift."""
    dest = tmp_path / "c"
    main(["init", str(dest), "--ci", "azure"])
    ref = ".azure-pipelines/install-uv.yml"
    template = yaml.safe_load((dest / ref).read_text())
    (install,) = template["steps"]
    assert "sha256sum --check --strict" in install["bash"]
    assert "System.AccessToken" not in (dest / ref).read_text()

    doc = yaml.safe_load((dest / "azure-pipelines.yml").read_text())
    for stage in doc["stages"]:
        steps = _azure_steps(doc, stage["stage"])
        assert {"template": ref} in steps, stage["stage"]
        assert not any("uv-${arch}" in s.get("bash", "") for s in steps)


_CI = resources.files("htrflow_converter") / "ci"
_GITHUB = _CI / "github" / ".github" / "workflows" / "render.yml"
_AZURE = _CI / "azure" / "azure-pipelines.yml"


def test_every_github_action_is_pinned_to_a_commit():
    """audit 0923 S-4: tags move, and this workflow's Render job holds
    `contents: write` on the campaigns repo's main branch. A pin is the full
    commit SHA, with the tag it was taken from as a comment."""
    text = _GITHUB.read_text()
    uses = re.findall(r"uses: (\S+)(.*)", text)
    assert uses
    for action, comment in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), (action, comment)


def test_the_converter_is_installed_from_a_commit_in_both_ci_flavours():
    """A tag like `v0.5.0` can be moved to other code; a commit cannot. The
    two flavours install the same one."""
    refs = {
        str(ci): yaml.safe_load(ci.read_text())[key]["CONVERTER_REF"]
        for ci, key in ((_GITHUB, "env"), (_AZURE, "variables"))
    }
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs.values()), refs
    assert len(set(refs.values())) == 1, refs


def test_the_github_flavour_checks_the_kyverno_tarball_like_the_azure_one():
    """The Azure flavour verified the Kyverno CLI against a committed
    SHA-256; the GitHub one piped curl into tar, in the job before the one
    that pushes to main."""
    github = yaml.safe_load(_GITHUB.read_text())
    azure = yaml.safe_load(_AZURE.read_text())
    for key in ("KYVERNO_VERSION", "KYVERNO_SHA256_LINUX_X64", "KYVERNO_SHA256_LINUX_ARM64"):
        assert github["env"][key] == azure["variables"][key], key
    step = next(
        s for s in github["jobs"]["policy"]["steps"]
        if s.get("name", "").startswith("Install the Kyverno CLI")
    )  # fmt: skip
    assert "sha256sum --check --strict" in step["run"]
    assert "| tar" not in step["run"]
