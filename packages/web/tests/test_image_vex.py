"""The OpenVEX statements for the web and converter images hold for the code.

`.docker/distroless.openvex.json` tells Trivy that some CVEs in the distroless
base's Debian packages do not affect these images. Most of those statements
rest on a module nobody imports: an expat flaw cannot be reached by an image
that parses no XML, a tarfile-filter flaw by one that extracts no archive.
That is a claim about code, so it is checked against the code: the two
packages the images install (packages/web, packages/converter with the
`hook` extra) and every locked dependency of theirs. A change that starts
parsing XML or extracting a tar archive fails here until the statement it
breaks is removed.

html.parser is the exception. The web service parses the HTML pages built
into its static directory, to hash their inline scripts for its
Content-Security-Policy, so that statement says the parser's input cannot be
controlled by an adversary instead. What is checked for it is that claim:
every `feed()` in the service's own code parses text read from a file, and
every such file is one the static-file server resolved or a path built from
the static directory.
"""

from __future__ import annotations

import ast
import functools
import json
import re
from collections.abc import Set
from importlib import metadata
from pathlib import Path

import pytest
import tomllib
from packaging.markers import Marker

REPO = Path(__file__).resolve().parents[3]
VEX = REPO / ".docker" / "distroless.openvex.json"
SOURCES = [REPO / "packages" / "web" / "src", REPO / "packages" / "converter" / "src"]
# What each image installs from the workspace lock, extras included.
ROOTS = {"htrflow-web": (), "htrflow-converter": ("hook",)}

# Every module through which Python reaches libexpat.
EXPAT = ("xml", "pyexpat", "xmlrpc", "plistlib")
ABSENT = "absent"
STATIC_ONLY = "static-only"

#: The basis of every statement in the VEX file. A statement with no entry
#: here fails the test: whoever adds one says what it rests on.
RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "CVE-2026-76642": (ABSENT, ()),  # util-linux mount(8): not shipped
    "CVE-2026-78408": (ABSENT, ()),  # util-linux nsenter(1): not shipped
    "CVE-2026-78409": (ABSENT, ()),  # util-linux mount(8): not shipped
    "CVE-2026-78410": (ABSENT, ()),  # util-linux mount(8): not shipped
    "CVE-2025-69720": (ABSENT, ()),  # ncurses infocmp(1): not shipped
    "CVE-2026-82049": (ABSENT, ("tarfile",)),
    "CVE-2026-7210": (ABSENT, EXPAT),
    "CVE-2026-66046": (ABSENT, EXPAT),
    "CVE-2026-76956": (ABSENT, EXPAT),
    "CVE-2026-76957": (ABSENT, EXPAT),
    "CVE-2026-93990": (ABSENT, EXPAT),
    "CVE-2026-15308": (STATIC_ONLY, ("html.parser",)),
}
JUSTIFICATIONS = {
    ABSENT: {"vulnerable_code_not_present", "vulnerable_code_not_in_execute_path"},
    STATIC_ONLY: {"vulnerable_code_cannot_be_controlled_by_adversary"},
}

#: Locked dependencies that do import tarfile, and why that is not the
#: extraction the CVE is about. A new one fails the test.
TARFILE_IN_DEPENDENCIES = {
    "dulwich/archive.py",  # writes archives (`dulwich archive`), extracts none
    "dateutil/zoneinfo/__init__.py",  # reads its own bundled zoneinfo tarball
    "dateutil/zoneinfo/rebuild.py",  # a maintainer tool that rebuilds it
}

_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w, ()]+)|import\s+([\w., ]+))", re.M
)


def _statements() -> list[dict]:
    return json.loads(VEX.read_text())["statements"]


def _modules(text: str) -> set[str]:
    """Every module a Python file imports, dotted, including the submodules
    named in `from x import y` (which may be modules)."""
    found: set[str] = set()
    for frm, names, plain in _IMPORT.findall(text):
        if frm:
            found.add(frm)
            found.update(f"{frm}.{n.strip()}" for n in names.strip("() ").split(","))
        else:
            found.update(n.strip().split(" as ")[0] for n in plain.split(","))
    return found


def _hits(modules: Set[str], banned: tuple[str, ...]) -> set[str]:
    return {m for m in modules for b in banned if m == b or m.startswith(b + ".")}


def _own_files() -> list[Path]:
    return [p for root in SOURCES for p in sorted(root.rglob("*.py"))]


def _locked_closure(with_extras: bool = True) -> dict[str, list[str]]:
    """The workspace lock's dependency closure of what the two images
    install (no dev groups): package -> the markers it is reached under,
    empty for unconditionally."""
    lock = {
        p["name"]: p for p in tomllib.loads((REPO / "uv.lock").read_text())["package"]
    }
    seen: dict[str, list[str]] = {}
    todo = [(name, extras if with_extras else ()) for name, extras in ROOTS.items()]
    while todo:
        name, extras = todo.pop()
        package = lock[name]
        deps = list(package.get("dependencies", []))
        for extra in extras:
            deps += package.get("optional-dependencies", {}).get(extra, [])
        for dep in deps:
            first = dep["name"] not in seen
            seen.setdefault(dep["name"], []).append(dep.get("marker", ""))
            if first:
                todo.append((dep["name"], tuple(dep.get("extra", ()))))
    for root in ROOTS:
        seen.pop(root, None)
    return seen


@functools.cache
def _dependency_files() -> dict[str, str]:
    """relative path -> source, for every .py file of every locked dependency
    installed in this venv. The test venv (`uv sync --all-packages`) carries
    the default dependencies, so a missing one is a failure unless its
    marker excludes this interpreter; the converter's `hook` extra (dulwich)
    is scanned when the venv has it (`--all-extras`) and otherwise checked
    in the image, where its tarfile use was reviewed."""
    extra_only = set(_locked_closure()) - set(_locked_closure(with_extras=False))
    files: dict[str, str] = {}
    for name, markers in sorted(_locked_closure().items()):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            needed = any(not m or Marker(m).evaluate() for m in markers)
            assert not needed or name in extra_only, (
                f"{name} is locked for the images but not installed here"
            )
            continue
        for f in dist.files or []:
            if f.suffix == ".py":
                files[str(f)] = Path(dist.locate_file(f)).read_text(errors="replace")
    return files


def test_every_statement_has_a_basis_and_names_exact_package_versions() -> None:
    statements = _statements()
    assert sorted(s["vulnerability"]["name"] for s in statements) == sorted(RULES)
    for s in statements:
        kind, _ = RULES[s["vulnerability"]["name"]]
        assert s["status"] == "not_affected"
        assert s["justification"] in JUSTIFICATIONS[kind], s["vulnerability"]
        assert s["impact_statement"].strip()
        for product in s["products"]:
            # A version-scoped Debian purl: the next package update retires it.
            assert re.fullmatch(r"pkg:deb/debian/[a-z0-9.+-]+@[^@?]+", product["@id"])


@functools.cache
def _dependency_modules() -> dict[str, frozenset[str]]:
    """relative path -> the modules it imports, for every dependency file:
    parsed once for every check below."""
    return {rel: frozenset(_modules(text)) for rel, text in _dependency_files().items()}


#: Each set of modules a statement says is never imported, with the
#: statements that rest on it. html.parser rests on another basis for the
#: service's own code (checked further down), so only the dependencies are
#: held to it here: a locked dependency parsing HTML would be input nobody
#: has vetted.
BANS: dict[tuple[str, ...], list[str]] = {}
for _cve, (_kind, _banned) in sorted(RULES.items()):
    if _banned:
        BANS.setdefault(_banned, []).append(_cve)


@pytest.mark.parametrize("banned", sorted(BANS), ids=lambda b: "+".join(b))
def test_no_code_in_the_images_imports_what_a_statement_says_is_unused(
    banned: tuple[str, ...],
) -> None:
    cves = ", ".join(BANS[banned])
    tar = "tarfile" in banned
    if RULES[BANS[banned][0]][0] == ABSENT:
        for path in _own_files():
            text = path.read_text()
            assert not _hits(_modules(text), banned), (
                f"{path.relative_to(REPO)} breaks {cves}"
            )
            assert not (tar and "unpack_archive" in text), (
                f"{path.relative_to(REPO)} breaks {cves}"
            )
    for rel, modules in _dependency_modules().items():
        if tar and rel in TARFILE_IN_DEPENDENCIES:
            continue
        assert not _hits(modules, banned), f"dependency {rel} breaks {cves}"
        if tar:
            assert not re.search(r"\bunpack_archive\(", _dependency_files()[rel]), (
                f"dependency {rel} breaks {cves}"
            )


def test_the_tarfile_exceptions_still_exist() -> None:
    """An exception for a file that no longer imports tarfile is stale."""
    files = _dependency_files()
    for rel in TARFILE_IN_DEPENDENCIES:
        if rel.startswith("dulwich/") and rel not in files:
            continue  # the hook extra, absent from a default test venv
        assert rel in files and "tarfile" in _modules(files[rel]), rel


def _static_reads(module: ast.Module) -> None:
    """Every `feed()` parses text read from a file under the static directory.

    A feeder is a function that calls `.feed(x)`, where x is its own
    parameter or text it read itself. Every call of a feeder, and every
    feed of text read in place, must pass a name bound in the calling
    function from `.read_text()`/`.read_bytes()`, whose receiver either
    names the static directory or runs in a StaticFiles subclass (which
    only resolves files inside its directory)."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(module):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing(node, kind):
        while node in parents:
            node = parents[node]
            if isinstance(node, kind):
                return node
        return None

    def read_source(name: str, fn: ast.AST):
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in ("read_text", "read_bytes")
            ):
                return node.value.func.value
        return None

    def check_read(arg: ast.AST, call: ast.AST) -> None:
        fn = enclosing(call, (ast.FunctionDef, ast.AsyncFunctionDef))
        assert isinstance(arg, ast.Name) and fn is not None, ast.unparse(call)
        receiver = read_source(arg.id, fn)
        assert receiver is not None, (
            f"parses text not read from a file: {ast.unparse(call)}"
        )
        cls = enclosing(fn, ast.ClassDef)
        serves_static = cls is not None and any(
            "StaticFiles" in ast.unparse(base) for base in cls.bases
        )
        assert "static" in ast.unparse(receiver).lower() or serves_static, (
            f"parses a file outside the static directory: {ast.unparse(call)}"
        )

    feeders: set[str] = set()
    for call in ast.walk(module):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        if call.func.attr != "feed" or len(call.args) != 1:
            continue
        fn = enclosing(call, (ast.FunctionDef, ast.AsyncFunctionDef))
        assert fn is not None, ast.unparse(call)
        arg = call.args[0]
        params = {a.arg for a in fn.args.args}
        if isinstance(arg, ast.Name) and arg.id in params:
            feeders.add(fn.name)
        else:
            check_read(arg, call)
    for call in ast.walk(module):
        if not isinstance(call, ast.Call):
            continue
        name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        if name in feeders:
            assert len(call.args) == 1, ast.unparse(call)
            check_read(call.args[0], call)


def test_html_is_parsed_only_from_the_static_directory() -> None:
    for path in _own_files():
        text = path.read_text()
        if not _hits(_modules(text), ("html.parser",)) and "HTMLParser" not in text:
            continue
        assert "packages/web/" in path.as_posix(), (
            f"{path}: only the web service parses HTML"
        )
        _static_reads(ast.parse(text))


def test_the_static_only_check_rejects_request_data() -> None:
    """The check above is only as good as what it refuses."""
    bad = """
from html.parser import HTMLParser
def _page(html):
    p = HTMLParser()
    p.feed(html)
    return p
async def route(request):
    body = await request.body()
    return _page(body)
"""
    with pytest.raises(AssertionError):
        _static_reads(ast.parse(bad))
    elsewhere = """
from html.parser import HTMLParser
from pathlib import Path
def _page(html):
    HTMLParser().feed(html)
def any_file(path):
    text = Path(path).read_text()
    return _page(text)
"""
    with pytest.raises(AssertionError):
        _static_reads(ast.parse(elsewhere))
