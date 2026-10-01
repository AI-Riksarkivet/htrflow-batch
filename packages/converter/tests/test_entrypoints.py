"""Every program the charts, the compose stack and the Dockerfiles start
from an image's venv is a console script a workspace package declares.

The results proxy's Deployment once ran `/app/.venv/bin/htrflow-results`
from a web image built before that script existed, and nothing in CI
noticed. This holds the other half of that contract: a command that names
a script no package's `[project.scripts]` declares (renamed, or never
added) fails here. Whether the *pinned* image already has it is the
release's job: a release re-pins the digests to images built from this
tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib
import yaml

REPO = Path(__file__).resolve().parents[3]
VENV_BIN = "/app/.venv/bin/"
#: The venv's own interpreter is no console script.
INTERPRETERS = {"python", "python3"}

#: `command:` / `entrypoint:` with a flow list on the same line, possibly
#: spanning lines, or a block list below it.
_KEY = re.compile(r"^(\s*)(?:-\s+)?(command|entrypoint):[ \t]*(.*)$", re.MULTILINE)
_DOCKER_ENTRYPOINT = re.compile(r"^(?:ENTRYPOINT|CMD)\s+(\[.*\])\s*$", re.MULTILINE)


def declared_scripts() -> set[str]:
    scripts: set[str] = set()
    for pyproject in sorted(REPO.glob("packages/*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        scripts |= set(project.get("scripts", {}))
    return scripts


def _argv(text: str, match: re.Match[str]) -> list[str]:
    """The list after one `command:`/`entrypoint:` key, read as YAML."""
    rest = match.group(3).split(" #")[0].strip()
    if rest.startswith("["):
        body = text[match.start(3) :]
        return yaml.safe_load(body[: body.index("]") + 1])
    if rest:
        return [yaml.safe_load(rest)]
    indent = len(match.group(1))
    items = []
    for line in text[match.end() + 1 :].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) < indent or not line.lstrip().startswith(
            "- "
        ):
            break
        items.append(yaml.safe_load(line.lstrip()[2:]))
    return items


def started_commands() -> list[tuple[str, list[str]]]:
    """(file, argv) for every command a chart template, the compose file or
    a Dockerfile's ENTRYPOINT/CMD names."""
    found = []
    sources = [
        *sorted(REPO.glob("charts/*/templates/**/*.yaml")),
        REPO / ".docker" / "docker-compose.yml",
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        found += [(path, _argv(text, m)) for m in _KEY.finditer(text)]
    for path in sorted((REPO / ".docker").glob("*.dockerfile")):
        text = path.read_text(encoding="utf-8")
        found += [
            (path, yaml.safe_load(m.group(1)))
            for m in _DOCKER_ENTRYPOINT.finditer(text)
        ]
    return [
        (str(path.relative_to(REPO)), [str(a) for a in argv])
        for path, argv in found
        if argv
    ]


def _ours(argv: list[str]) -> str | None:
    """The console script or `-m` module a command starts from a workspace
    package, or None for anything else (a shell, another image's binary)."""
    program = argv[0].removeprefix(VENV_BIN)
    if program in INTERPRETERS:
        return f"-m {argv[2]}" if argv[1:2] == ["-m"] else None
    return program if argv[0].startswith(VENV_BIN) else None


def _started(argv: list[str]) -> bool:
    name = _ours(argv)
    if name is None or name.startswith("-m "):
        module = name and name.removeprefix("-m ")
        return module is None or any(REPO.glob(f"packages/*/src/{module}/__main__.py"))
    return name in declared_scripts()


OURS = [(f, argv) for f, argv in started_commands() if _ours(argv)]


def test_the_known_entrypoints_are_found():
    """The scan is not vacuous: it sees the proxy's command in the chart and
    the compose file, and each image's own entrypoint."""
    started = {(f, _ours(argv)) for f, argv in OURS}
    assert started >= {
        ("charts/htrflow-batch/templates/results.yaml", "htrflow-results"),
        (".docker/docker-compose.yml", "htrflow-results"),
        (".docker/htrflow-web.dockerfile", "htrflow-web"),
        (".docker/htrflow-campaigns.dockerfile", "htrflow-campaigns"),
        (".docker/htrflow-batch.dockerfile", "-m htrflow_batch"),
    }


@pytest.mark.parametrize(
    "path,argv", OURS, ids=[f"{f}:{_ours(argv)}" for f, argv in OURS]
)
def test_every_program_started_from_the_venv_is_declared(path: str, argv: list[str]):
    assert _started(argv), (
        f"{path} starts {_ours(argv)}: no packages/*/pyproject.toml"
        " [project.scripts] declares that script, or no package has that"
        " module's __main__.py"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["/app/.venv/bin/htrflow-gone", "--flag"],
        ["python", "-m", "htrflow_gone"],
    ],
)
def test_a_renamed_entrypoint_is_caught(argv: list[str]):
    """The check itself: a block-style command is read, and a script or
    module nobody provides is refused."""
    text = "containers:\n  - name: x\n    command:\n" + "".join(
        f"      - {a}\n" for a in argv
    )
    (match,) = _KEY.finditer(text)
    assert _argv(text, match) == argv
    assert not _started(argv)


def test_the_release_gate_checks_every_program_the_tree_starts():
    """`dagger call entrypoints-published` holds each pinned digest to
    .dagger/published.go's `publishedEntrypoints`; this holds that list to
    the tree, so a new command cannot slip past the release gate."""
    go = (REPO / ".dagger" / "published.go").read_text(encoding="utf-8")
    block = go[go.index("var publishedEntrypoints") :]
    block = block[: block.index("\n}\n")]
    gated = set(re.findall(r'"([a-z][a-z-]+)"', block)) - {"web", "campaigns"}
    started = {_ours(argv) for _, argv in OURS} - {None}
    assert {s for s in started if not s.startswith("-m ")} <= gated
