"""Static serving: the SPA, Universal Viewer and /config.js out of STATIC_DIR.

This is what the retired nginx image did (chart 0.3.0's viewer template):
serve the campaign browser at /, UV at /uv.html, map the extensionless /log
to adapter-static's log.html, and send three security headers on everything.
The API routes must still win over the static mount.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from htrflow_web.app import SECURITY_HEADERS, NoCluster, create_app


class EmptyReader:
    cfg = SimpleNamespace(public_results_base="https://results.example.org")

    def list_jobs(self) -> list[dict]:
        return []

    def list_warmups(self) -> list[dict]:
        return []

    def get_job(self, namespace: str, name: str) -> dict | None:
        return None


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<h1>campaign browser</h1>")
    (tmp_path / "log.html").write_text("<h1>run log</h1>")
    (tmp_path / "alto.html").write_text("<h1>alto viewer</h1>")
    (tmp_path / "uv.html").write_text("<h1>universal viewer</h1>")
    (tmp_path / "config.js").write_text('window.API_BASE = "/api/v1";\n')
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


def test_api_routes_win_over_static(client: TestClient):
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_healthz_still_wins(client: TestClient):
    assert client.get("/healthz").json() == {"ok": True}


def test_unknown_page_is_404_not_the_spa(client: TestClient):
    assert client.get("/nope").status_code == 404


@pytest.mark.parametrize("path", ["/", "/api/v1/jobs", "/uv.html"])
def test_security_headers_on_every_response(client: TestClient, path: str):
    headers = client.get(path).headers
    for name, value in SECURITY_HEADERS.items():
        assert headers[name] == value


def test_no_static_dir_still_serves_the_api(tmp_path: Path):
    """A local `uv run htrflow-web` has no built site; the API must not care."""
    client = TestClient(create_app(EmptyReader(), static_dir=tmp_path / "absent"))
    assert client.get("/api/v1/jobs").status_code == 200
    assert client.get("/").status_code == 404


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/jobs", "/api/v1/jobs/ns/name"])
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
