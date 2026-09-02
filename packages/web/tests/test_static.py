"""Static serving: the SPA, Universal Viewer and /config.js out of STATIC_DIR.

This is what the retired nginx image did (chart 0.3.0 templates/viewer.yaml):
serve the campaign browser at /, UV at /uv.html, map the extensionless /log
to adapter-static's log.html, and send three security headers on everything.
The API routes must still win over the static mount.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from htrflow_web.app import SECURITY_HEADERS, create_app


class EmptyReader:
    cfg = SimpleNamespace(public_results_base="https://results.example.org")

    def list_jobs(self) -> list[dict]:
        return []


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<h1>campaign browser</h1>")
    (tmp_path / "log.html").write_text("<h1>run log</h1>")
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
