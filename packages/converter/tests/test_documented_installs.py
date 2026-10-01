"""The install commands the documentation prints, rendered as written.

Deploy and Dev cluster each print the `helm` command an operator copies.
When the chart gained a required value, the Deploy page's command stopped
rendering and nothing noticed. These tests take every `helm install` /
`helm upgrade --install` from those pages' fenced blocks, fill the
placeholders in with documentation addresses, and render it as
`helm template`: a value the chart starts to require fails here, not on an
operator's first install.

The CI templates `htrflow-campaigns init --ci` writes render the chart's
policies the same way, at the release the campaigns repo pins: their
command is rendered against this checkout's chart, which a release pins
them to.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PAGES = ("docs/getting-started/deploy.md", "docs/development/dev-cluster.md")

#: One sample per placeholder the pages use (RFC 5737 addresses). A new
#: placeholder fails `test_every_placeholder_has_a_sample` until it has one.
SAMPLES = {
    "<namespace>": "htr-batch",
    "<web-front-host>": "htr.example.org",
    "<node-address>": "192.0.2.20",
    "<apiserver-address>": "192.0.2.10",
    "<iiif-source-cidr>": "203.0.113.0/24",
    "<s3-endpoint-cidr>": "198.51.100.10/32",
    "<pod-cidr>": "10.244.0.0/16",
    "<service-cidr>": "10.96.0.0/12",
    "<client-cidr>": "192.0.2.0/24",
    "<hcp|none>": "hcp",
}
_PLACEHOLDER = re.compile(r"<[a-z|-]+>")
_FENCE = re.compile(r"^ *```bash\n(.*?)^ *```", re.MULTILINE | re.DOTALL)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not on PATH")


def _installs(page: str) -> list[str]:
    """Each `helm install …` / `helm upgrade --install …` on the page, its
    continuation lines joined."""
    text = (REPO / page).read_text(encoding="utf-8")
    commands = []
    for block in _FENCE.findall(text):
        for command in re.sub(r"\\\n\s*", " ", block).splitlines():
            command = command.strip()
            if command.startswith(("helm install ", "helm upgrade --install ")):
                commands.append(command)
    return commands


INSTALLS = [(page, command) for page in PAGES for command in _installs(page)]


def test_each_page_prints_an_install():
    assert {page for page, _ in INSTALLS} == set(PAGES)


@pytest.mark.parametrize("page,command", INSTALLS, ids=[p for p, _ in INSTALLS])
def test_every_placeholder_has_a_sample(page: str, command: str):
    assert set(_PLACEHOLDER.findall(command)) <= set(SAMPLES), command


@pytest.mark.parametrize("page,command", INSTALLS, ids=[p for p, _ in INSTALLS])
def test_the_documented_install_renders(page: str, command: str):
    for placeholder, sample in SAMPLES.items():
        command = command.replace(placeholder, sample)
    argv = shlex.split(command)
    verb = 3 if argv[1:3] == ["upgrade", "--install"] else 2
    result = subprocess.run(
        ["helm", "template", *argv[verb:]], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0, f"{page}: {command}\n{result.stderr}"


#: The chart's policy render in each CI template, with the paths it checks
#: the chart out to.
CI_TEMPLATES = {
    "github": (
        "packages/converter/src/htrflow_converter/ci/github/.github/workflows/render.yml",
        ".htrflow-batch/charts/htrflow-batch",
    ),
    "azure": (
        "packages/converter/src/htrflow_converter/ci/azure/azure-pipelines.yml",
        "$AGENT_TEMPDIRECTORY/htrflow-batch/charts/htrflow-batch",
    ),
}


@pytest.mark.parametrize("flavour", CI_TEMPLATES)
def test_the_ci_templates_policy_render_renders(flavour: str):
    path, chart = CI_TEMPLATES[flavour]
    text = (REPO / path).read_text(encoding="utf-8")
    start = text.index("helm template htrflow-batch")
    command = re.sub(
        r"\\\n\s*",
        " ",
        text[start : text.index("\n", text.index("--show-only", start))],
    )
    env = dict(
        re.findall(r"^\s*(POLICY_[A-Z_]+):\s*\"?([^\"\n]*)\"?", text, re.MULTILINE)
    )
    command = command.replace(chart, "charts/htrflow-batch").replace('"${sets[@]}"', "")
    for name, value in env.items():
        command = command.replace(f"${name}", value)
    argv = shlex.split(command.split(" > ")[0])
    result = subprocess.run(
        ["helm", *argv[1:]], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0, f"{path}: {command}\n{result.stderr}"
