"""Static serving: the SPA, Universal Viewer and /config.js out of STATIC_DIR.

This is what the retired nginx image did (chart 0.3.0's viewer template):
serve the campaign browser at /, UV at /uv.html, map the extensionless /log
to adapter-static's log.html, and send three security headers on everything.
The API routes must still win over the static mount.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from htrflow_web.app import SECURITY_HEADERS, NoCluster, create_app, uv_csp

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


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<h1>campaign browser</h1>")
    (tmp_path / "log.html").write_text("<h1>run log</h1>")
    (tmp_path / "alto.html").write_text("<h1>alto viewer</h1>")
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
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp


def test_the_viewers_own_inline_script_is_allowed_by_its_hash(client: TestClient):
    """UV ships its bootstrap inline, so `script-src 'self'` alone would
    serve a blank viewer. The hash lets exactly that one script run and
    nothing an injected tag could add."""
    csp = client.get("/uv.html").headers["Content-Security-Policy"]
    script = UV_HTML.split("<script>")[1].split("</script>")[0]
    style = UV_HTML.split("<style>")[1].split("</style>")[0]
    assert f"'sha256-{_sha256(script)}'" in csp
    assert f"'sha256-{_sha256(style)}'" in csp


def test_every_other_page_keeps_the_plain_header(client: TestClient):
    """The SPA must not inherit the viewer's policy: its own meta CSP is the
    stricter one, and a header cannot be looser than it anyway."""
    for path in ("/", "/log", "/api/v1/jobs"):
        headers = client.get(path).headers
        assert headers["Content-Security-Policy"] == "frame-ancestors 'none'"


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
    "path", ["/healthz", "/api/v1/version", "/api/v1/jobs", "/api/v1/jobs/ns/name"]
)
def test_head_is_answered_by_the_route_not_the_static_mount(
    client: TestClient, path: str
):
    """FastAPI does not add HEAD to a GET route; without it these fall through
    to the mount and 404 (or, for the decoy, serve a file)."""
    resp = client.head(path)
    assert resp.status_code in (200, 404)
    assert resp.headers["content-type"] == "application/json"
    assert resp.text == ""


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
