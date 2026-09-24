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
the static directory -- traced through the code, not taken on a name -- and,
at run time, that everything the parser is fed is a file of that directory.
"""

from __future__ import annotations

import ast
import functools
import json
import re
from collections.abc import Set
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib
from fastapi.testclient import TestClient
from packaging.markers import Marker

from htrflow_web.app import NoCluster, create_app

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


#: A module imported by name at run time: `importlib.import_module("x")`,
#: `import_module("x")` or `__import__("x")`, the name a string literal.
_DYNAMIC_IMPORT = re.compile(r"""\b(?:import_module|__import__)\(\s*["']([\w.]+)["']""")


def _modules(text: str) -> set[str]:
    """Every module a Python file imports, dotted, including the submodules
    named in `from x import y` (which may be modules) and the ones imported
    by a literal name at run time."""
    found: set[str] = set()
    if "import_module" in text or "__import__" in text:  # rare: skip the scan
        found.update(_DYNAMIC_IMPORT.findall(text))
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
        text = _dependency_files()[rel]
        if tar and "unpack_archive" in text:  # rare: skip the pattern
            assert not re.search(r"\bunpack_archive\(", text), (
                f"dependency {rel} breaks {cves}"
            )


@pytest.mark.parametrize(
    "source",
    [
        'importlib.import_module("xml.etree.ElementTree")',
        "import_module('xml.dom.minidom')",
        '__import__("pyexpat")',
        'mod = __import__( "xml" )',
    ],
)
def test_a_module_imported_by_name_counts_as_imported(source: str) -> None:
    """An import statement is not the only way in: a module loaded by name
    reaches libexpat just the same."""
    assert _hits(_modules(source), EXPAT)


def test_the_tarfile_exceptions_still_exist() -> None:
    """An exception for a file that no longer imports tarfile is stale."""
    files = _dependency_files()
    for rel in TARFILE_IN_DEPENDENCIES:
        if rel.startswith("dulwich/") and rel not in files:
            continue  # the hook extra, absent from a default test venv
        assert rel in files and "tarfile" in _modules(files[rel]), rel


#: Where the static directory enters the service: the directory the app is
#: created with, and the file a StaticFiles subclass resolved inside it
#: (``file_response``'s ``full_path``). A value is from the static directory
#: only if it is built from one of these.
STATIC_ROOTS = {("create_app", "static_dir")}
RESOLVED_FILE = ("file_response", "full_path")

_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


def _static_reads(module: ast.Module) -> None:
    """Every `feed()` parses text read from a file under the static directory.

    A feeder is a function that calls `.feed(x)` with x its own parameter.
    Every call of a feeder, and every feed of anything else, must pass a
    name bound in the calling function from `.read_text()`/`.read_bytes()`
    whose receiver is built from the static directory: from a
    ``STATIC_ROOTS`` parameter or ``RESOLVED_FILE``, through local names,
    string constants, imported callables and parameters whose every caller
    in the module passes such a value. Nothing is taken on its name."""
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

    imported: set[str] = set()
    constants: set[str] = set()
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants |= {t.id for t in node.targets if isinstance(t, ast.Name)}

    def serves_static(fn) -> bool:
        cls = enclosing(fn, ast.ClassDef)
        return cls is not None and any(
            "StaticFiles" in ast.unparse(base) for base in cls.bases
        )

    def params(fn) -> list[str]:
        return [a.arg for a in fn.args.args]

    def callers(fn):
        """Every call of ``fn`` in the module, as (call, the argument
        expression per parameter name)."""
        method = enclosing(fn, ast.ClassDef) is not None
        for call in ast.walk(module):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Name) and func.id == fn.name and not method:
                names = params(fn)
            elif isinstance(func, ast.Attribute) and func.attr == fn.name and method:
                names = params(fn)[1:]  # bound: self is not passed
            else:
                continue
            bound = dict(zip(names, call.args))
            bound |= {k.arg: k.value for k in call.keywords if k.arg}
            yield call, bound

    def from_static(expr, fn, seen: frozenset = frozenset()) -> bool:
        if isinstance(expr, ast.Constant):
            return isinstance(expr.value, str)
        if isinstance(expr, ast.Name):
            if fn is not None and expr.id in params(fn):
                return param_from_static(fn, expr.id, seen)
            if fn is not None:
                values = [
                    n.value
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == expr.id for t in n.targets
                    )
                ]
                if values:
                    return all(from_static(v, fn, seen) for v in values)
            return expr.id in constants
        if isinstance(expr, ast.BinOp):
            return from_static(expr.left, fn, seen) and from_static(
                expr.right, fn, seen
            )
        if isinstance(expr, ast.BoolOp):
            return all(from_static(v, fn, seen) for v in expr.values)
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Attribute):
                # A method of a static value (`UV_PATH.lstrip("/")`), or a
                # function of an imported module (`os.path.realpath`).
                root = func
                while isinstance(root, ast.Attribute):
                    root = root.value
                callee = isinstance(root, ast.Name) and root.id in imported
                if not callee and not from_static(func.value, fn, seen):
                    return False
            elif not (isinstance(func, ast.Name) and func.id in imported):
                return False
            args = [*expr.args, *(k.value for k in expr.keywords)]
            return all(from_static(a, fn, seen) for a in args)
        return False

    def param_from_static(fn, name: str, seen: frozenset) -> bool:
        if (fn.name, name) in STATIC_ROOTS and not serves_static(fn):
            return True
        if (fn.name, name) == RESOLVED_FILE and serves_static(fn):
            return True
        if (fn.name, name) in seen:
            return False  # a cycle proves nothing
        seen = seen | {(fn.name, name)}
        calls = list(callers(fn))
        return bool(calls) and all(
            name in bound and from_static(bound[name], enclosing(call, _FUNCTION), seen)
            for call, bound in calls
        )

    def check_read(arg: ast.AST, call: ast.AST) -> None:
        fn = enclosing(call, _FUNCTION)
        assert isinstance(arg, ast.Name) and fn is not None, ast.unparse(call)
        reads = [
            n.value.func.value
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr in ("read_text", "read_bytes")
        ]
        assert reads, f"parses text not read from a file: {ast.unparse(call)}"
        for receiver in reads:
            assert from_static(receiver, fn), (
                f"parses a file not proven to be under the static directory: "
                f"{ast.unparse(call)}"
            )

    feeders: set[str] = set()
    for call in ast.walk(module):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        if call.func.attr != "feed" or len(call.args) != 1:
            continue
        fn = enclosing(call, _FUNCTION)
        assert fn is not None, ast.unparse(call)
        arg = call.args[0]
        if isinstance(arg, ast.Name) and arg.id in params(fn):
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
    # Named for the static directory, and handed a path from the request.
    by_name = """
from html.parser import HTMLParser
from pathlib import Path
def _page(html):
    HTMLParser().feed(html)
def _evil(untrusted_static):
    text = untrusted_static.read_text()
    return _page(text)
def create_app(static_dir):
    def route(path):
        return _evil(Path(path))
"""
    with pytest.raises(AssertionError, match="not proven"):
        _static_reads(ast.parse(by_name))


def test_the_static_only_check_follows_the_directory_it_is_given() -> None:
    """...and as good as what it lets through: the shapes the service uses,
    a path built from the app's static directory and a file the static
    server resolved, pass."""
    good = """
from html.parser import HTMLParser
from pathlib import Path
import os
from fastapi.staticfiles import StaticFiles
DEFAULT = "/app/static"
PAGE = "/uv.html"
def _page(html):
    HTMLParser().feed(html)
def hashes(static):
    html = (static / PAGE.lstrip("/")).read_text()
    return _page(html)
class Site(StaticFiles):
    def file_response(self, full_path):
        real = os.path.realpath(full_path)
        return self._policy(real)
    def _policy(self, real):
        text = Path(real).read_text()
        return _page(text)
def create_app(static_dir):
    return hashes(Path(static_dir or DEFAULT))
"""
    _static_reads(ast.parse(good))


class _SiteReader(NoCluster):
    """A reader with a results base, so the SPA's own policy is built too;
    every API call is refused, which is all this test asks of it."""

    cfg = SimpleNamespace(
        public_results_base="https://results.example.org", namespaces=("htr-a",)
    )


def test_every_html_parsed_at_run_time_is_a_file_of_the_static_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """The claim itself, at run time: the app is started over a built site
    and asked for every kind of thing a client can ask for -- pages, the
    viewer and its aliases, documents with and without a policy, a 304,
    the API, a miss, markup in the path, the query and the body -- and
    everything the parser was ever fed is the text of a file in the static
    directory."""
    meta = '<meta http-equiv="content-security-policy" content="base-uri \'self\'">'
    site = {
        "index.html": f"<html><head>{meta}</head><body>browser</body></html>",
        "log.html": f"<html><head>{meta}</head><body>log</body></html>",
        "uv.html": "<html><head><script>UV.init()</script></head></html>",
        "other.html": "<html><body><script>alert(1)</script></body></html>",
        "icon.svg": "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        "examples/page.htm": "<html><body>example</body></html>",
        "_app/start.js": "// bundle",
    }
    static = tmp_path / "static"
    for name, text in site.items():
        (static / name).parent.mkdir(parents=True, exist_ok=True)
        (static / name).write_text(text)
        # The same names one level up: a read that left the directory
        # parses one of these.
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(f"<html>outside the site: {name}</html>")
    fed: list[str] = []
    feed = HTMLParser.feed

    def recording(self, data: str) -> None:
        fed.append(data)
        feed(self, data)

    monkeypatch.setattr(HTMLParser, "feed", recording)
    client = TestClient(create_app(_SiteReader(), static_dir=static))
    evil = "<meta http-equiv=content-security-policy content=x>"
    for path in (
        "/", "/log", "/log.html", "/uv", "/uv.html", "/uv.html/", "/other.html",
        "/icon.svg", "/examples/page.htm", "/_app/start.js", "/config.js",
        "/healthz", "/api/v1/version", "/api/v1/jobs", "/api/v1/jobs/htr-a/kyrk",
        "/nope", f"/{evil}", f"/log?x={evil}",
    ):  # fmt: skip
        client.get(path)
    etag = client.get("/other.html").headers["etag"]
    client.get("/other.html", headers={"If-None-Match": etag})
    client.post("/log", content=evil, headers={"Content-Type": "text/html"})
    assert fed, "the app parsed nothing: the check would prove nothing"
    assert set(fed) <= set(site.values())
    assert len(set(fed)) == 6, "every document in the site, the bundle not"
