"""Static serving: the SPA, Universal Viewer and /config.js out of STATIC_DIR.

This is what the retired nginx image did (chart 0.3.0's viewer template):
serve the campaign browser at /, UV at /uv.html, map the extensionless /log
to adapter-static's log.html, and send three security headers on everything.
The API routes must still win over the static mount.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from htrflow_web.app import (
    SECURITY_HEADERS,
    STRICT_CSP,
    NoCluster,
    create_app,
    uv_csp,
)

#: The shape the built Universal Viewer really has (verified against
#: /app/static/uv.html in the web image): one inline <style>, one inline
#: <script>, and its bundle loaded from a file beside it.
UV_HTML = """<html><head>
<link rel="stylesheet" href="uv.css" />
<script type="text/javascript" src="umd/UV.js"></script>
<style>
      body { margin: 0; }
</style>
</head><body><h1>universal viewer</h1><div id="uv"></div>
<script>
      document.addEventListener("DOMContentLoaded", function() { UV.init("uv"); });
</script>
</body></html>"""


#: What the SvelteKit build's pages carry (kit.csp, frontend/svelte.config.js):
#: the page's own policy, as a <meta http-equiv> tag in its head.
SPA_META = (
    '<meta http-equiv="content-security-policy" '
    "content=\"object-src 'none'; script-src 'self'; base-uri 'self'\">"
)


def _spa(body: str) -> str:
    return f"<html><head>{SPA_META}</head><body>{body}</body></html>"


def _sha256(body: str) -> str:
    return base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()


class EmptyReader:
    cfg = SimpleNamespace(public_results_base="https://results.example.org")

    def list_jobs(self) -> list[dict]:
        return []

    def list_warmups(self) -> list[dict]:
        return []

    def get_job(self, namespace: str, name: str) -> dict | None:
        return None

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return None  # no record either: the campaign really is a 404

    def list_configmaps(self) -> list[dict]:
        return []

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return []

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = ""
    ) -> None:
        raise AssertionError("nothing here is a campaign to write about")


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text(_spa("<h1>campaign browser</h1>"))
    (tmp_path / "log.html").write_text(_spa("<h1>run log</h1>"))
    (tmp_path / "alto.html").write_text(_spa("<h1>alto viewer</h1>"))
    (tmp_path / "uv.html").write_text(UV_HTML)
    (tmp_path / "config.js").write_text("STATIC FALLBACK\n")
    (tmp_path / "_app").mkdir()
    (tmp_path / "_app" / "start.js").write_text("// bundle")
    # A decoy the API route must shadow: static is mounted at /, so only
    # route order keeps /api/v1/jobs an API response.
    (tmp_path / "api" / "v1").mkdir(parents=True)
    (tmp_path / "api" / "v1" / "jobs").write_text("DECOY")
    return tmp_path


@pytest.fixture
def client(static_dir: Path) -> TestClient:
    return TestClient(create_app(EmptyReader(), static_dir=static_dir))


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("/", "campaign browser"),
        ("/log", "run log"),
        ("/alto", "alto viewer"),
        ("/uv.html", "universal viewer"),
        ("/config.js", "API_BASE"),
        ("/_app/start.js", "bundle"),
    ],
)
def test_serves_the_built_site(client: TestClient, path: str, marker: str):
    resp = client.get(path)
    assert resp.status_code == 200
    assert marker in resp.text


def test_config_js_is_the_services_own_answer(client: TestClient):
    """The page's results base and the API's are the same setting; a copy in
    the image that an operator had to keep in step would be wrong exactly
    when it mattered (2026-09-14 audit)."""
    resp = client.get("/config.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    assert 'window.API_BASE = "/api/v1";' in resp.text
    assert 'window.RESULTS_BASE = "https://results.example.org";' in resp.text
    assert "STATIC" not in resp.text, "never the file in the image"


def test_config_js_says_nothing_when_there_is_no_cluster(static_dir: Path):
    """Site-only mode has no results base to name, and the run-log route
    reads an empty one as "nobody said"."""
    client = TestClient(create_app(NoCluster(), static_dir=static_dir))
    assert 'window.RESULTS_BASE = "";' in client.get("/config.js").text


def test_api_routes_win_over_static(client: TestClient):
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_healthz_still_wins(client: TestClient):
    assert client.get("/healthz").json() == {"ok": True}


def test_unknown_page_is_404_not_the_spa(client: TestClient):
    assert client.get("/nope").status_code == 404


@pytest.mark.parametrize("path", ["/", "/api/v1/jobs", "/api/v1/version", "/uv.html"])
def test_security_headers_on_every_response(client: TestClient, path: str):
    """The viewer's Content-Security-Policy is its own (it has no <meta> CSP
    to carry one), so what every response shares is the rest of the set and
    the framing rule inside the policy."""
    headers = client.get(path).headers
    for name, value in SECURITY_HEADERS.items():
        if name == "Content-Security-Policy":
            assert "frame-ancestors 'none'" in headers[name]
        else:
            assert headers[name] == value


def test_the_viewer_gets_a_csp_of_its_own(client: TestClient):
    """The SPA's sources are governed by its build's own <meta> CSP; the
    viewer is a third-party page with no meta tag at all, and until now the
    only thing forbidden on it was framing (2026-09-14 audit)."""
    csp = client.get("/uv.html").headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp, "what the header always carried"
    script_src = [d for d in csp.split(";") if d.strip().startswith("script-src")][0]
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in csp


def test_the_viewers_own_inline_script_is_allowed_by_its_hash(client: TestClient):
    """UV ships its bootstrap inline, so `script-src 'self'` alone would
    serve a blank viewer. The hash lets exactly that one script run and
    nothing an injected tag could add."""
    csp = client.get("/uv.html").headers["Content-Security-Policy"]
    script = UV_HTML.split("<script>")[1].split("</script>")[0]
    assert f"'sha256-{_sha256(script)}'" in csp


def _hashes(csp: str) -> list[str]:
    script_src = [d for d in csp.split(";") if d.strip().startswith("script-src")]
    return [t.strip("'")[7:] for t in script_src[0].split() if "sha256-" in t]


@pytest.mark.parametrize(
    ("html", "bodies"),
    [
        # End tags a browser closes a script on, and a regex looking for
        # exactly `</script>` did not (code scanning 117).
        ("<script>a()</script >", ["a()"]),
        ("<script>b()</script\n>", ["b()"]),
        ("<SCRIPT>c()</SCRIPT foo>", ["c()"]),
        ("<script>d()</script ><script>e()</script>", ["d()", "e()"]),
        # Not end tags at all: they are text of the script they sit in.
        ('<script>f("</scriptx>")</script>', ['f("</scriptx>")']),
        ('<script>g("<b>x</b>")</script>', ['g("<b>x</b>")']),
        # Only a `src` attribute makes a script a file -- `data-src` is not.
        ("<script data-src=x>h()</script>", ["h()"]),
        ('<script src="umd/UV.js"></script>', []),
        ("<script src=umd/UV.js>ignored()</script>", []),
    ],
)
def test_every_inline_script_is_hashed_as_the_browser_reads_it(
    tmp_path: Path, html: str, bodies: list[str]
):
    """The hashes are taken over exactly the text a browser runs as each
    inline script -- one it read differently is a script the policy blocks,
    and a viewer that does not start."""
    (tmp_path / "uv.html").write_text(f"<html><head>{html}</head></html>")
    csp = uv_csp(tmp_path)
    assert csp is not None
    assert _hashes(csp) == [_sha256(b) for b in bodies]


def test_the_viewers_styles_are_unsafe_inline_with_no_hash_beside_it(
    client: TestClient,
):
    """UV lays itself out through `style=` attributes it writes at runtime,
    which no hash can name: with them blocked the viewer rendered as bare
    buttons and an image at the foot of the page. A hash next to
    'unsafe-inline' would make a browser ignore the latter, so the style
    directive carries the keyword and nothing else."""
    csp = client.get("/uv.html").headers["Content-Security-Policy"]
    style_src = [d for d in csp.split(";") if d.strip().startswith("style-src")][0]
    assert style_src.strip() == "style-src 'self' 'unsafe-inline'"
    style = UV_HTML.split("<style>")[1].split("</style>")[0]
    assert f"'sha256-{_sha256(style)}'" not in csp


#: Every request path the static mount answers with uv.html: the extensionless
#: retry adds ".html", and StaticFiles normalises the path before it looks the
#: file up, so a trailing slash, an empty segment or a dot segment names the
#: same file. `/uv` and `/uv.html/` are the aliases the 2026-09-17 audit proved
#: XSS through. The last three only reach the app from a client that sends
#: the path as written (a browser or httpx would normalise or misread them),
#: so they are sent as a raw ASGI request.
UV_ALIASES = ["/uv.html", "/uv", "/uv.html/", "/uv/"]
RAW_UV_ALIASES = ["//uv.html", "/./uv.html", "/_app/../uv.html"]


@pytest.mark.parametrize("path", UV_ALIASES)
def test_every_path_that_serves_the_viewer_gets_its_csp(client: TestClient, path):
    """The viewer's policy follows the file served, not the path asked for:
    matched on the path, `/uv` and `/uv.html/` served the same page with only
    `frame-ancestors 'none'`, and the ALTO panel's raw line HTML then ran as
    script on the web front's origin (2026-09-17 audit)."""
    resp = client.get(path)
    assert resp.status_code == 200 and "universal viewer" in resp.text
    assert (
        resp.headers["Content-Security-Policy"]
        == client.get("/uv.html").headers["Content-Security-Policy"]
    )
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def _raw_get(app, path: str) -> tuple[int, dict[bytes, bytes]]:
    """GET `path` exactly as written, which no HTTP client here will do."""
    sent: list[dict] = []
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent[0]["status"], dict(sent[0]["headers"])


@pytest.mark.parametrize("path", RAW_UV_ALIASES)
def test_an_unnormalised_path_to_the_viewer_gets_its_csp(static_dir: Path, path):
    status, headers = _raw_get(create_app(EmptyReader(), static_dir=static_dir), path)
    assert status == 200
    assert headers[b"content-security-policy"].decode() == uv_csp(static_dir)


def test_a_revalidated_viewer_keeps_its_csp(client: TestClient):
    """A 304 for the viewer is still the viewer's response."""
    etag = client.get("/uv").headers["etag"]
    resp = client.get("/uv", headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_every_other_page_keeps_the_plain_header(client: TestClient):
    """The SPA must not inherit the viewer's policy: its own meta CSP is the
    stricter one, and a header cannot be looser than it anyway."""
    for path in ("/", "/log"):
        assert client.get(path).headers["Content-Security-Policy"] == SPA_CSP
    headers = client.get("/api/v1/jobs").headers
    assert headers["Content-Security-Policy"] == "frame-ancestors 'none'"


@pytest.mark.parametrize(
    "name",
    ["examples/demo.html", "collection.htm", "other.xhtml", "icon.svg", "EXTRA.HTML"],
)
def test_a_page_with_no_policy_of_its_own_gets_the_strictest(static_dir, name):
    """The whole Universal Viewer build is copied into the site, and only
    uv.html has a policy: any other document it ships was served with
    nothing but `frame-ancestors 'none'` (2026-09-23 audit). A page that
    states no policy of its own runs nothing and loads nothing here."""
    path = static_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html><body><script>alert(1)</script></body></html>")
    client = TestClient(create_app(EmptyReader(), static_dir=static_dir))
    resp = client.get(f"/{name}")
    assert resp.status_code == 200
    csp = resp.headers["Content-Security-Policy"]
    assert csp == STRICT_CSP
    assert "default-src 'none'" in csp and "sandbox" in csp
    assert "frame-ancestors 'none'" in csp


SPA_CSP = "frame-ancestors 'none'; connect-src 'self' https://results.example.org/"


def test_the_spas_pages_may_fetch_only_the_api_and_the_results_bucket(
    client: TestClient,
):
    """Their meta policy is fixed at build time and the results base is not
    known until the service starts, so the header carries the one directive
    the meta tag cannot: the page reads its own API and the results bucket,
    and nothing else (2026-09-23 audit). Both policies apply, and nothing
    else in the meta tag is narrowed."""
    for path in ("/", "/log", "/alto", "/log.html"):
        assert client.get(path).headers["Content-Security-Policy"] == SPA_CSP


@pytest.mark.parametrize(
    ("base", "source"),
    [
        ("https://results.example.org/bucket", "https://results.example.org/bucket/"),
        ("http://localhost:30900/htr-results", "http://localhost:30900/htr-results/"),
        ("https://user:pw@s3.example.org/b", "https://s3.example.org/b/"),
        # A `;` or `,` would end the directive or the policy: encoded.
        ("https://s3.example.org/a;b,c", "https://s3.example.org/a%3Bb%2Cc/"),
    ],
)
def test_the_results_base_is_one_well_formed_source(static_dir, base, source):
    reader = EmptyReader()
    reader.cfg = SimpleNamespace(public_results_base=base)
    client = TestClient(create_app(reader, static_dir=static_dir))
    csp = client.get("/log").headers["Content-Security-Policy"]
    assert csp == f"frame-ancestors 'none'; connect-src 'self' {source}"


def test_with_no_results_base_the_spa_is_not_narrowed(static_dir: Path):
    """Site-only mode names no base, and the run log then reads any http(s)
    URL, as it did before there was one -- a connect-src would break that."""
    client = TestClient(create_app(NoCluster(), static_dir=static_dir))
    for path in ("/", "/log"):
        assert client.get(path).headers["Content-Security-Policy"] == (
            "frame-ancestors 'none'"
        )


@pytest.mark.parametrize(
    ("path", "policy"),
    [
        ("/", SPA_CSP),
        ("/log", SPA_CSP),
        ("/examples/demo.html", STRICT_CSP),
        ("/icon.svg", STRICT_CSP),
        ("/uv.html", None),  # the viewer's own, compared below
    ],
)
def test_a_revalidated_document_keeps_the_policy_of_the_file(static_dir, path, policy):
    """A 304 carries none of the file's headers but a few, and a browser
    updates what it stored from the 304 -- so a reload ran the page under
    `frame-ancestors 'none'` alone: no connect-src, no sandbox (2026-09-23
    review). The policy is the file's, on the 200 and the 304 alike."""
    for name in ("examples/demo.html", "icon.svg"):
        (static_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (static_dir / name).write_text("<html><body>x</body></html>")
    client = TestClient(create_app(EmptyReader(), static_dir=static_dir))
    first = client.get(path)
    again = client.get(path, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    expected = policy or uv_csp(static_dir)
    assert first.headers["Content-Security-Policy"] == expected
    assert again.headers["Content-Security-Policy"] == expected


def test_scripts_and_styles_keep_the_plain_header(client: TestClient):
    assert client.get("/_app/start.js").headers["Content-Security-Policy"] == (
        "frame-ancestors 'none'"
    )


def test_a_viewer_nobody_built_gets_the_headers_anyway(tmp_path: Path):
    """A source checkout has no uv.html to hash; the page is a 404 and the
    response still carries every header the middleware sends."""
    client = TestClient(create_app(EmptyReader(), static_dir=tmp_path))
    assert uv_csp(tmp_path) is None
    assert client.get("/uv.html").headers["X-Content-Type-Options"] == "nosniff"


def test_no_static_dir_still_serves_the_api(tmp_path: Path):
    """A local `uv run htrflow-web` has no built site; the API must not care."""
    client = TestClient(create_app(EmptyReader(), static_dir=tmp_path / "absent"))
    assert client.get("/api/v1/jobs").status_code == 200
    assert client.get("/").status_code == 404


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/healthz", 200),
        ("/api/v1/version", 200),
        ("/api/v1/jobs", 200),
        # The route's own 404 -- no such campaign -- not the mount's.
        ("/api/v1/jobs/ns/name", 404),
    ],
)
def test_head_is_answered_by_the_route_not_the_static_mount(
    client: TestClient, path: str, status: int
):
    """FastAPI does not add HEAD to a GET route; without it these fall through
    to the mount and 404 (or, for the decoy, serve a file). The mount's 404
    is JSON with an empty HEAD body too, so the status alone is not enough:
    HEAD must describe the very body GET sends."""
    resp = client.head(path)
    get = client.get(path)
    assert resp.status_code == status == get.status_code
    assert resp.headers["content-type"] == "application/json"
    assert resp.text == ""
    assert resp.headers["content-length"] == get.headers["content-length"]
    if status == 404:
        assert get.json() == {"detail": "job not found"}


def test_head_on_a_page(client: TestClient):
    assert client.head("/log").status_code == 200


def test_root_is_not_retried_as_html(tmp_path: Path):
    """The extensionless retry must never turn "/" into ".html": with no
    index.html the root is a plain 404, not a 500 from a nonsense lookup."""
    (tmp_path / "uv.html").write_text("<h1>universal viewer</h1>")
    client = TestClient(create_app(EmptyReader(), static_dir=tmp_path))
    assert client.get("/").status_code == 404
    assert client.get("/uv.html").status_code == 200


class TestSiteOnly:
    """HTRFLOW_WEB_SITE_ONLY (the compose stack): the site, no cluster."""

    @pytest.fixture
    def client(self, static_dir: Path) -> TestClient:
        return TestClient(create_app(NoCluster(), static_dir=static_dir))

    @pytest.mark.parametrize("path", ["/", "/log", "/alto", "/uv.html", "/config.js"])
    def test_site_still_served(self, client: TestClient, path: str):
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", ["/api/v1/jobs", "/api/v1/jobs/htr-batch/kyrk"])
    def test_api_is_a_clean_503(self, client: TestClient, path: str):
        resp = client.get(path)
        assert resp.status_code == 503
        assert "HTRFLOW_WEB_SITE_ONLY" in resp.json()["detail"]

    def test_healthz_still_ok(self, client: TestClient):
        assert client.get("/healthz").json() == {"ok": True}

    def test_version_still_answers(self, client: TestClient):
        """The header shows a version on the compose stack too: it is this
        process's own package, nothing a cluster could tell it."""
        assert client.get("/api/v1/version").json()["version"] == "dev"

    def test_no_progress_reader_is_built(self, static_dir: Path, monkeypatch):
        """A site-only process has no bucket and every /api/v1/... route
        503s before it would ever call progress.fetch -- the HTTP client a
        ProgressReader opens would have nothing to ask."""
        from htrflow_web import app as app_mod

        built = []
        monkeypatch.setattr(
            app_mod, "ProgressReader", lambda *a, **k: built.append(1) or object()
        )
        create_app(NoCluster(), static_dir=static_dir)
        assert built == []
